"""Offline verification; run as python -m infrastructure.verify_audit export.json --key-file PATH."""
import argparse
import json
from pathlib import Path
from core.runtime.audit import key_id, verify_export


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export')
    parser.add_argument('--key-file',action='append',required=True)
    parser.add_argument('--previous',help='Previously retained authenticated export to detect truncation')
    args=parser.parse_args()
    keys={}
    for name in args.key_file:
        key=Path(name).read_bytes().strip()
        keys[key_id(key)]=key
    result=verify_export(json.loads(Path(args.export).read_text()),keys,
                         json.loads(Path(args.previous).read_text()) if args.previous else None)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
