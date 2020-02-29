#!/usr/bin/env python3
import os
import sys
import threading
import traceback
import queue
from pathlib import Path
from datetime import datetime

import requests


BASE_URL = os.getenv("TUNASYNC_UPSTREAM_URL", "https://api.github.com/repos/")
WORKING_DIR = os.getenv("TUNASYNC_WORKING_DIR")
REPOS = ["googlefonts/noto-fonts",
         "googlefonts/noto-cjk",
         "googlefonts/noto-emoji",
         "be5invis/Sarasa-Gothic",
         "z4yx/GoAuthing",
         "VSCodium/vscodium",
         "openark/orchestrator",
         "git-lfs/git-lfs",
         "prometheus/prometheus",
         "AdoptOpenJDK/openjdk8-binaries",
         "AdoptOpenJDK/openjdk9-binaries",
         "AdoptOpenJDK/openjdk10-binaries",
         "AdoptOpenJDK/openjdk11-binaries",
         "AdoptOpenJDK/openjdk12-binaries",
         "AdoptOpenJDK/openjdk13-binaries",
         "commercialhaskell/stackage-content",
         ]

# connect and read timeout value
TIMEOUT_OPTION = (7, 10)


def requests_download(remote_url: str, dst_file: Path, remote_ts: float):
    # NOTE the stream=True parameter below
    with requests.get(remote_url, stream=True) as r:
        r.raise_for_status()
        with open(dst_file, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024**2):
                if chunk:  # filter out keep-alive new chunks
                    f.write(chunk)
                    # f.flush()
        os.utime(dst_file, (remote_ts, remote_ts))


def downloading_worker(q):
    while True:
        item = q.get()
        if item is None:
            break

        url, dst_file, working_dir, updated = item

        print("downloading", url, "to",
              dst_file.relative_to(working_dir), flush=True)
        try:
            requests_download(url, dst_file, updated)
        except Exception:
            print("Failed to download", url, flush=True)
            if dst_file.is_file():
                dst_file.unlink()

        q.task_done()


def create_workers(n):
    task_queue = queue.Queue()
    for i in range(n):
        t = threading.Thread(target=downloading_worker, args=(task_queue, ))
        t.start()
    return task_queue


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--working-dir", default=WORKING_DIR)
    parser.add_argument("--repo", type=str, nargs='*', default=REPOS,
                        help='repositories to download (e.g. --repo be5invis/Sarasa-Gothic googlefonts/noto-emoji)')
    parser.add_argument("--workers", default=1, type=int,
                        help='number of concurrent downloading jobs')
    parser.add_argument("--fast-skip", action='store_true',
                        help='do not verify size and timestamp of existing files')
    args = parser.parse_args()

    if args.working_dir is None:
        raise Exception("Working Directory is None")

    working_dir = Path(args.working_dir)
    task_queue = create_workers(args.workers)
    remote_filelist = []
    cleaning = False

    for repo in args.repo:
        repo_local = working_dir / Path(repo)
        print(f"syncing {repo} to {repo_local}")
        try:
            r = requests.get(f"{args.base_url}{repo}/releases")
            r.raise_for_status()
            releases = r.json()
        except:
            traceback.print_exc()
            break

        for latest in releases:
            if not latest['draft'] and not latest['prerelease']:
                break
        else:
            print("Error: No release version found")
            continue

        name = latest['name'] or latest['tag_name']
        if len(name) == 0:
            print("Error: Unnamed release")
            continue

        if len(latest['assets']) == 0:
            url = latest['tarball_url']
            updated = datetime.strptime(latest['published_at'], '%Y-%m-%dT%H:%M:%SZ').timestamp()
            dst_file = repo_local / name / 'repo-snapshot.tar.gz'
            remote_filelist.append(dst_file.relative_to(working_dir))

            if dst_file.is_file():
                print("skipping", dst_file.relative_to(working_dir), flush=True)
            else:
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                task_queue.put((url, dst_file, working_dir, updated))

        for asset in latest['assets']:
            if '/' in asset['name'] or '\\' in asset['name']:
                print(f"Error: Invalid file name {asset['name']}")
                continue
            url = asset['browser_download_url']
            updated = datetime.strptime(asset['updated_at'], '%Y-%m-%dT%H:%M:%SZ').timestamp()
            dst_file = repo_local / name / asset['name']
            remote_filelist.append(dst_file.relative_to(working_dir))

            if dst_file.is_file():
                if args.fast_skip:
                    print("fast skipping", dst_file.relative_to(working_dir), flush=True)
                    continue
                else:
                    stat = dst_file.stat()
                    local_filesize = stat.st_size
                    local_mtime = stat.st_mtime
                    # print(f"{local_filesize} vs {asset['size']}")
                    # print(f"{local_mtime} vs {updated}")
                    if asset['size'] == local_filesize and local_mtime == updated:
                        print("skipping", dst_file.relative_to(working_dir), flush=True)
                        continue
            else:
                dst_file.parent.mkdir(parents=True, exist_ok=True)

            task_queue.put((url, dst_file, working_dir, updated))
    else:
        cleaning = True

    # block until all tasks are done
    task_queue.join()
    # stop workers
    for i in range(args.workers):
        task_queue.put(None)

    if cleaning:
        local_filelist = []
        for local_file in working_dir.glob('**/*'):
            if local_file.is_file():
                local_filelist.append(local_file.relative_to(working_dir))

        for old_file in set(local_filelist) - set(remote_filelist):
            print("deleting", old_file, flush=True)
            old_file = working_dir / old_file
            old_file.unlink()


if __name__ == "__main__":
    main()


# vim: ts=4 sw=4 sts=4 expandtab