# Demo: Baker Street Lab / Cluster A

A fully fabricated example: a fictional lab ("Baker Street Lab") on a fictional cluster
("Cluster A"), with users named after characters from *The Adventures of Sherlock Holmes*
(public domain). No real lab, cluster, or person is involved.

It exists to let you see the actual dashboard render without needing Slurm access, and to give
the screenshots in the main README something real to come from.

## How it works

`fakebin/` has stand-ins for `sacctmgr`, `sshare`, `sacct` and `scontrol` that print fabricated
but internally consistent data (defined in `fakebin/_fixtures.py`) instead of talking to a real
scheduler. Putting `fakebin/` first on `PATH` makes the real, unmodified `bin/build_usage_page.py`
run against this fake data instead of a real cluster. Nothing here touches Slurm, and nothing here
touches the real `config.env` or `index.html` at the repo root (`HPCUSAGE_ROOT` points the builder
at this folder instead).

The dates in the fake job history are generated relative to "now" each time it runs, including a
deliberate usage reset 20 days ago, so the page's reset-detection genuinely finds it, the same way
it would on a real cluster.

## Running it

```
./run_demo.sh
```

Then open `index.html` in this folder. Re-run it any time, e.g. after changing
`bin/build_usage_page.py`, to check your change against a known dataset.
