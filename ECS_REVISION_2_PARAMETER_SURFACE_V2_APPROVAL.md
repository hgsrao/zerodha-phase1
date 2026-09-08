# ECS Revision 2 parameter surface V2 approval

## Decision

The project owner authorized re-freezing the current canonical Revision 2
parameter-surface payload on 2026-09-08.

## Approved contract

- Contract ID: `ECS_REVISION_2_PARAMETER_SURFACE_V2`
- SHA-256: `7712e701c73cb8ec04baabaf77baa73d8a9ada6337792d4212ed0840d931ba19`
- Target surface: 69 values (47 calibratable, 22 fixed)
- Separate safety contract: 20 immutable values

## Reason

The prior Version 1 identity was introduced with the `saturation_exit_bars`
expansion but was never validated against its canonical identity payload. The
Version 2 identity is derived from the current payload, including the Version
2 contract identifier, and is enforced by the canonical registry regression
test.

## Governance requirements

Any future parameter-surface change must update this approval record, use a
new contract version, compute a new payload hash, and pass
`verify_frozen_identity()` before downstream adapters or calibration runs are
allowed.
