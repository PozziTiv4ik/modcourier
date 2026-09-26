# Recover a release

Start with `status --json` and `inspect --json`. Keep .modcourier/state.json:
it records acknowledged uploads and requests whose outcome is unknown.

## One platform failed

Fix the reported problem, then rerun the same publish command. A completed upload
is checked and skipped while the unfinished platform continues. Successful releases
are never deleted to simulate a rollback across independent services.

## The connection failed during upload

ModCourier never automatically repeats a POST. The server may have accepted the
file before the response was lost.

First rerun inspect. If the platform now lists the exact file hash, the operation
can reconcile automatically. If not, open the author's Files tab, including files
under review. Use the exact original JAR, release ID and hashes from the journal.

When a remote file exists and its API data is available:

```sh
python modcourier.py recover curseforge 1.0.0+fabric.mc1.21.1 \
  --file-id 987654 \
  --evidence "https://authors.curseforge.com/ — checked Files tab, upload found" \
  --project /path/to/mod
```

The command verifies the remote hash before recording acknowledgement.

Only after verifying in the authenticated author dashboard that **no matching
upload exists**, record that observation:

```sh
python modcourier.py recover curseforge 1.0.0+fabric.mc1.21.1 \
  --absent \
  --evidence "https://authors.curseforge.com/ — checked approved and pending files; no upload" \
  --project /path/to/mod
```

Then run publish again. --absent is a manual recovery assertion; the CLI cannot
verify what a browser user saw. An empty public search is insufficient evidence.
If a pending file cannot yet be verified through the API, retain the uncertain
state until its result can be established. Do not upload another copy.

For an uncertain **project creation**, find the draft in the author account and
bind its ID. The publisher will not create a second project while creation is
uncertain.

## Same version, different content

Confirm which build should be released. Set a new mod version in its own build
configuration, rebuild, and update artifact paths if needed. ModCourier never
changes the mod's version or overwrites an existing remote binary to bypass this
conflict.

## A process crashed

The lock file contains a PID and timestamp. Check that no publisher for this mod
is running, then remove **only .modcourier/publish.lock**. Keep the journal.
Run inspect; interrupted requests remain uncertain until reconciled.

## A project disappeared

A 404 can indicate private, deleted, pending or inaccessible content. Check the
author dashboard and account access. Do not bind --new or erase the existing ID
to turn this into an apparently new project.

## Output shows pending moderation

Publication cannot accelerate the platform's review queue. status reports the
current API view. A server acknowledgement or exit code 0 does not mean approval.
The journal's last acknowledged status is marked as unverified if a current
remote check is unavailable.
