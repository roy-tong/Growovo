from __future__ import annotations


def resolve_go(go=None, dry_run=False):
    if dry_run:
        return False
    return True if go is None else bool(go)


def add_execution_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--go", action="store_true", default=None,
        help="显式实跑；省略时默认也是 live，仍需通过授权与执行门")
    group.add_argument(
        "--dry-run", action="store_true", default=False,
        help="只预览，不执行外部写动作")
