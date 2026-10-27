"""R3 online rollback check: compare the finalized frame checksums of two games (SSBU_RB_NET_LOG files, lines "f <frame> h <hex>").
    python tests/rbn_cmp.py <file A> <file B> [--min-frames N] [--strict]
Prints how many frames both files have, whether they are identical, and the first difference. (hs12) A file can hold several
matches of one session: a line "m <n> id <match id>" starts match n, whose frames count from 1 again; each match is compared
on its own (and the match ids too). Exit 0 = enough common frames in every match, with matching hashes and match ids.
Empty logs never pass. --strict also requires identical, contiguous frame coverage from frame 1. No game content."""
import argparse
import sys


def read(path):
    out, ids, m = {}, {}, 1
    with open(path, errors='replace') as src:
        for line, ln in enumerate(src, 1):
            p = ln.split()
            if p and p[0] == 'm':
                if len(p) not in (2, 4) or (len(p) == 4 and p[2] != 'id'):
                    raise ValueError('%s:%d: malformed match record' % (path, line))
                m = int(p[1])
                match_id = p[3] if len(p) == 4 else '-'
                if m < 1 or (m in ids and ids[m] != match_id):
                    raise ValueError('%s:%d: invalid or conflicting match id' % (path, line))
                ids[m] = match_id
            elif p and p[0] == 'f':
                if len(p) != 4 or p[2] != 'h':
                    raise ValueError('%s:%d: malformed frame record' % (path, line))
                frame = int(p[1])
                if frame < 1 or not 0 <= int(p[3], 16) <= 0xffffffffffffffff:
                    raise ValueError('%s:%d: invalid frame or hash' % (path, line))
                frames = out.setdefault(m, {})
                if frame in frames and frames[frame] != p[3]:
                    raise ValueError('%s:%d: conflicting hashes for frame %d' % (path, line, frame))
                frames[frame] = p[3]
    return out, ids


def compare(a, ia, b, ib, min_frames=1, strict=False):
    bad = 0
    matches = set(a) | set(b) | set(ia) | set(ib)
    if not matches:
        print('NOTHING TO COMPARE: neither log contains finalized frame hashes')
        return 2
    for m in sorted(matches):
        x, y = a.get(m, {}), b.get(m, {})
        common = sorted(set(x) & set(y))
        diff = [f for f in common if x[f] != y[f]]
        tag = 'match %d (id %s / %s)' % (m, ia.get(m, '-'), ib.get(m, '-')) if len(matches) > 1 or ia or ib else 'match'
        print('%s: A: %d frames (%s..%s), B: %d frames (%s..%s), common %d' % (
            tag, len(x), min(x, default='-'), max(x, default='-'), len(y), min(y, default='-'), max(y, default='-'), len(common)))
        if not common:
            print('  NOTHING TO COMPARE')
            bad = max(bad, 2)
        elif len(common) < min_frames:
            print('  INSUFFICIENT COVERAGE: %d common frames, at least %d required' % (len(common), min_frames))
            bad = max(bad, 2)
        if diff:
            print('  DIFFER: %d frames, first at frame %d (A %s, B %s)' % (len(diff), diff[0], x[diff[0]], y[diff[0]]))
            bad = max(bad, 1)
        elif len(common) >= min_frames:
            print('  IDENTICAL: all %d common frames (%d..%d)' % (len(common), common[0], common[-1]))
        if ia.get(m) != ib.get(m):
            print('  MATCH IDS DIFFER')
            bad = max(bad, 1)
        if strict:
            if set(x) != set(y):
                print('  FRAME COVERAGE DIFFERS')
                bad = max(bad, 1)
            for name, frames in (('A', x), ('B', y)):
                if frames and (min(frames) != 1 or len(frames) != max(frames)):
                    print('  %s: FRAME COVERAGE HAS GAPS OR DOES NOT START AT 1' % name)
                    bad = max(bad, 1)
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--min-frames', type=int, default=1, help='required common frames per match (default 1)')
    ap.add_argument('--strict', action='store_true', help='require identical contiguous frame coverage from frame 1')
    args = ap.parse_args(argv)
    if args.min_frames < 1:
        ap.error('--min-frames must be positive')
    try:
        (a, ia), (b, ib) = read(args.a), read(args.b)
    except (OSError, ValueError) as exc:
        print('INVALID COMPARISON INPUT: %s' % exc, file=sys.stderr)
        return 2
    return compare(a, ia, b, ib, args.min_frames, args.strict)


if __name__ == '__main__':
    sys.exit(main())
