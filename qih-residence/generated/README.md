# Generated — model-built tool-chains

Output of tool-chain synthesis: manifests + code that passed the static gate.
Execution is sandboxed (P1+: 2 GB / 30 s caps, no network unless declared,
output cap, kill on breach — FREE-BRAIN.md §6). Nothing here ever touches the
host, the model, or the ledger directly.

Anything not declared in the manifest is denied.