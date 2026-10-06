# Copied corrected source-audit diagram

```mermaid
flowchart TD
    N["Causal NIFTY + VIX context"] --> G["Grid measurements and state"]
    G --> E["ECS demand target and restoration ramp"]
    M["Realized-R merit weights"] --> W["Weighted sector water-filling"]
    E --> W
    W --> R["Five sector bay references"]
    R --> C["Admission cap: reference budget minus current exposure"]
    X["Current bay/fleet exposure"] --> C
    G --> D["Side-aligned macro droop"]
    D --> Z["Governor signed-z overspeed ceiling"]
    P["PA/ID candidate and studies conviction"] --> Z
    Z -->|"Strategic veto/derating in full governor mode"| F["FSR and protection checks; sizing and final gates"]
    C --> F
    F --> A["External paper fill: Engine A in symbol's mapped sector bay"]
    A --> H["Position control and protective exits"]
    H --> T["Authoritative trade close: realized R"]
    T --> M
    T --> O["Bay outer PID: future admission offset"]
    O --> Z
    A -. "Optional runtime, absent from default worker" .-> B["Handoff receipt: same sector bay, B ownership/CNC"]
```

Sector bay identity is independent of A/B ownership. Dashed handoff is optional and absent from the default candidate worker. Static source diagram is not execution evidence.
