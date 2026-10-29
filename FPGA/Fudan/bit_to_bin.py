"""Convert a local Procise-patched BIT to the existing Linux FPGA payload format."""
import argparse
import array
import hashlib
from pathlib import Path
import struct


def unpack_bit(path):
    data = Path(path).read_bytes()
    offset = 0

    def take(size):
        nonlocal offset
        value = data[offset:offset+size]
        if len(value) != size:
            raise ValueError('Truncated BIT')
        offset += size
        return value

    def u16():
        return struct.unpack('>H', take(2))[0]

    if take(u16()) != bytes.fromhex('0ff00ff00ff00ff000') or u16() != 1:
        raise ValueError('Invalid BIT header')
    fields = {}
    for tag in b'abcd':
        if take(1) != bytes([tag]):
            raise ValueError('Unexpected BIT field')
        fields[chr(tag)] = take(u16()).rstrip(b'\0').decode('ascii')
    if take(1) != b'e':
        raise ValueError('Missing BIT payload')
    payload = take(struct.unpack('>I', take(4))[0])
    if offset != len(data) or len(payload) % 4 or b'\xaa\x99\x55\x66' not in payload[:256]:
        raise ValueError('Invalid bitstream payload')
    if 'Patched=DISABLE_ICAP,VCCM' not in fields['a']:
        raise ValueError('Run the required Procise device conversion first')
    words = array.array('I')
    words.frombytes(payload)
    words.byteswap()
    return words.tobytes(), fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    payload, _ = unpack_bit(args.input)
    with args.output.open('xb') as destination:
        destination.write(payload)
    print(hashlib.sha256(payload).hexdigest(), args.output)


if __name__ == '__main__':
    main()
