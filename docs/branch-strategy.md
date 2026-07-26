# Branch Strategy

## Dependency Graph

```text
main
  └── memory-system
        ├── mixin
        └── redesign/agent-bridge-v2
```

`memory-system` is the only branch intended to merge directly into `main`. It carries the
canonical Memory Core, shared specs, common docs, and minimal fixture templates.

`mixin` depends on `memory-system` and should contain only Human Client deltas.

`redesign/agent-bridge-v2` depends on `memory-system` and should contain only Agent
Bridge deltas, real template catalogs, fixtures, and branch-specific docs/tests.

## Merge Policy

1. Review and merge `memory-system -> main`.
2. Retarget/review `mixin` against the merged Memory Core baseline.
3. Retarget/review Agent Bridge against the merged Memory Core baseline.
4. Integrate Human Client and Agent Bridge only after their own branch-specific gates pass.

Do not merge `mixin` into Agent Bridge or Agent Bridge into `mixin`.

## Diff Hygiene

`main...memory-system` may include Memory Core, specs, common docs, minimal template
fixtures, tests, and packaging. It must not include Human Client or Agent Bridge code.

`memory-system...mixin` may include Human Client code/docs/tests only.

`memory-system...redesign/agent-bridge-v2` may include Agent Bridge code/docs/tests,
fixtures, tooling, and real templates only.
