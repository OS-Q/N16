#!/usr/bin/env python3

import hashlib
import hmac
import os
import random
import struct

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.utils import int_to_bytes


def number_as_bignum_words(number):
    """
    Given a number, format result as a C array of words
    (little-endian, same as ESP32 RSA peripheral or mbedTLS)
    """
    result = []
    while number != 0:
        result.append('0x%08x' % (number & 0xFFFFFFFF))
        number >>= 32
    return '{ ' + ', '.join(result) + ' }'


def number_as_bytes(number, pad_bits=None):
    """
    Given a number, format as a little endian array of bytes
    """
    result = int_to_bytes(number)[::-1]
    while pad_bits is not None and len(result) < (pad_bits // 8):
        result += b'\x00'
    return result


def bytes_as_char_array(b):
    """
    Given a sequence of bytes, format as a char array
    """
    return '{ ' + ', '.join('0x%02x' % x for x in b) + ' }'


NUM_HMAC_KEYS = 3
NUM_MESSAGES = 10
NUM_CASES = 6


hmac_keys = [os.urandom(32) for x in range(NUM_HMAC_KEYS)]

messages = [random.randrange(0, 1 << 4096) for x in range(NUM_MESSAGES)]

with open('digital_signature_test_cases.h', 'w') as f:
    f.write('/* File generated by gen_digital_signature_tests.py */\n\n')

    # Write out HMAC keys
    f.write('#define NUM_HMAC_KEYS %d\n\n' % NUM_HMAC_KEYS)
    f.write('static const uint8_t test_hmac_keys[NUM_HMAC_KEYS][32] = {\n')
    for h in hmac_keys:
        f.write('     %s,\n' % bytes_as_char_array(h))
    f.write('};\n\n')

    # Write out messages
    f.write('#define NUM_MESSAGES %d\n\n' % NUM_MESSAGES)
    f.write('static const uint32_t test_messages[NUM_MESSAGES][4096/32] = {\n')
    for m in messages:
        f.write('        // Message %d\n' % messages.index(m))
        f.write('        %s,\n' % number_as_bignum_words(m))
    f.write('    };\n')
    f.write('\n\n\n')

    f.write('#define NUM_CASES %d\n\n' % NUM_CASES)
    f.write('static const encrypt_testcase_t test_cases[NUM_CASES] = {\n')

    for case in range(NUM_CASES):
        f.write('    { /* Case %d */\n' % case)

        iv = os.urandom(16)
        f.write('        .iv = %s,\n' % (bytes_as_char_array(iv)))

        hmac_key_idx = random.randrange(0, NUM_HMAC_KEYS)
        aes_key = hmac.HMAC(hmac_keys[hmac_key_idx], b'\xFF' * 32, hashlib.sha256).digest()

        sizes = [4096, 3072, 2048, 1024, 512]
        key_size = sizes[case % len(sizes)]

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
            backend=default_backend())

        priv_numbers = private_key.private_numbers()
        pub_numbers = private_key.public_key().public_numbers()
        Y = priv_numbers.d
        M = pub_numbers.n

        rr = 1 << (key_size * 2)
        rinv = rr % pub_numbers.n
        mprime = - rsa._modinv(M, 1 << 32)
        mprime &= 0xFFFFFFFF
        length = key_size // 32 - 1

        f.write('        .p_data = {\n')
        f.write('            .Y = %s,\n' % number_as_bignum_words(Y))
        f.write('            .M = %s,\n' % number_as_bignum_words(M))
        f.write('            .Rb = %s,\n' % number_as_bignum_words(rinv))
        f.write('            .M_prime = 0x%08x,\n' % mprime)
        f.write('            .length = %d, // %d bit\n' % (length, key_size))
        f.write('        },\n')

        # calculate MD from preceding values and IV

        # Y4096 || M4096 || Rb4096 || M_prime32 || LENGTH32 || IV128
        md_in = number_as_bytes(Y, 4096) + \
            number_as_bytes(M, 4096) + \
            number_as_bytes(rinv, 4096) + \
            struct.pack('<II', mprime, length) + \
            iv
        assert len(md_in) == 12480 / 8
        md = hashlib.sha256(md_in).digest()

        # generate expected C value from P bitstring
        #
        # Y4096 || M4096 || Rb4096 || M_prime32 || LENGTH32 || MD256 || 0x08*8
        p = number_as_bytes(Y, 4096) + \
            number_as_bytes(M, 4096) + \
            number_as_bytes(rinv, 4096) + \
            md + \
            struct.pack('<II', mprime, length) + \
            b'\x08' * 8

        assert len(p) == 12672 / 8

        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        c = encryptor.update(p) + encryptor.finalize()

        f.write('        .expected_c = %s,\n' % bytes_as_char_array(c))
        f.write('        .hmac_key_idx = %d,\n' % (hmac_key_idx))

        f.write('        // results of message array encrypted with these keys\n')
        f.write('        .expected_results = {\n')
        mask = (1 << key_size) - 1  # truncate messages if needed
        for m in messages:
            f.write('        // Message %d\n' % messages.index(m))
            f.write('      %s,' % (number_as_bignum_words(pow(m & mask, Y, M))))
        f.write('     },\n')
        f.write('     },\n')

    f.write('};\n')
