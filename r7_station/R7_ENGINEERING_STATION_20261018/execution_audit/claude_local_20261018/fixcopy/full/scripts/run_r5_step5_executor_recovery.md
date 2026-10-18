# Stage-A remote execution and recovery

The executor leaves the sealed protocol and candidate worker unchanged. The
laptop control plane uses the Python standard library; no worker redeployment
is required. Plan-only performs read-only identity checks and reconstructs the
next batch without writing state or launching workers.

For each laptop trial, `jobs/trial_NNN.json` beneath the local state directory
records launch intent, remote paths, job identity and the acknowledged PID.
The remote `/tmp/r5_step5_trial_NNN.job` directory is an exclusive launch claim.
It contains identity, detached supervisor PID and eventual exit status. The
supervisor redirects all standard streams, starts in a new session, and runs
the sealed candidate into a pending result file. Only a successful worker exit
publishes the final result with an atomic rename. Logs remain at
`/tmp/r5_step5_trial_NNN.log`.

Resume probes before launch. It harvests an existing final result (including
legacy foreground-worker output), reattaches to an identified running job, or
launches only if both result and claim are absent. Downloads are validated in
a temporary file before atomic local installation. Invalid local results fail
closed. A transport failure exits without deleting job state; invoke the same
bounded execution command to resume the incomplete batch after connectivity
returns. A failed job or ambiguous claim requires inspection, not an automatic
retry. Do not delete claims or results without resolving the job's status.
Remote `/tmp` files are not guaranteed to survive a laptop reboot.

A local execution lock prevents concurrent executors using the same state
directory. Both results must validate before the batch is sealed. Study replay
continues to ask whole batches and tell results in ascending trial-number
order, independent of completion order.

`--max-batches 1` limits batches processed by that invocation; it is not an
absolute batch-index limit. Once Batch 0 is COMPLETE, executing again can
release Trials 2–3. During hardening, use only `--plan-only` against production
state. Resume validation uses copied state and launch-blocking mocks.

Validation: `python -m pytest -q scripts/run_r5_step5_executor_tests.py`.
These tests run tiny synthetic detached processes, never candidate replay.
