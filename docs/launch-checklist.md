# Launch checklist

gitmem 1.0 ships only when both claims are true and explicitly signed off:

- **it works personally**
- **it works on benchmarks**

Use this page as the final ship/no-ship scorecard for a release candidate.

## Release candidate record

| Field | Value |
| --- | --- |
| Version / tag | |
| Commit SHA | |
| Candidate date | |
| Last accepted baseline | |
| Artifact root | `artifacts/release-gates/<stamp>/` |

## Sign-off: works personally

- [ ] Local dogfood repo completed and recorded under `artifacts/release-gates/<stamp>/dogfood/`
- [ ] GitHub-backed dogfood repo (`remote` or `hybrid`) completed and recorded under `artifacts/release-gates/<stamp>/dogfood/`
- [ ] Core workflows held up in repeated use: `init`, `capture`, `dream`, `search`, `inject`, `health`, and `sync`/governance where applicable
- [ ] Fresh `UMX_HOME` clean-room setup passed
- [ ] Fresh-home reattach + `gitmem sync` passed
- [ ] No open dogfood-found P0/P1 blockers remain

**Works personally sign-off:** ______________________________________

## Sign-off: works on benchmarks

- [ ] Local subset smoke artifacts exist:
  - `artifacts/release-gates/<stamp>/local/inject.smoke.json`
  - `artifacts/release-gates/<stamp>/local/long-memory.smoke.json`
  - `artifacts/release-gates/<stamp>/local/retrieval.smoke.json`
- [ ] **LongMemEval** release artifact exists:
  - `artifacts/release-gates/<stamp>/release/longmemeval.release.json`
- [ ] **HotpotQA** release artifact exists:
  - `artifacts/release-gates/<stamp>/release/hotpotqa.release.json`
- [ ] Compare outputs versus the last accepted baseline exist and are green:
  - `artifacts/release-gates/<stamp>/release/longmemeval.compare.json`
  - `artifacts/release-gates/<stamp>/release/hotpotqa.compare.json`
- [ ] LongMemEval result is acceptable for this candidate
- [ ] HotpotQA result is acceptable for this candidate

**Works on benchmarks sign-off:** __________________________________

## Open P0 / P1 bug check

- [ ] No open P0 bugs
- [ ] No open P1 bugs
- [ ] Bug tracker / query reviewed immediately before tagging

Bug query / notes:

---

## Release artifact locations / JSON outputs

| Artifact | Location | Notes |
| --- | --- | --- |
| Local dogfood record | | |
| GitHub-backed dogfood record | | |
| Inject smoke JSON | | |
| LongMemEval smoke JSON | | |
| HotpotQA smoke JSON | | |
| LongMemEval release JSON | | |
| HotpotQA release JSON | | |
| LongMemEval compare JSON | | |
| HotpotQA compare JSON | | |

## Follow-on benches after 1.0

- **Next best addition:** LoCoMo
- **Useful non-blocking stress benches:** RULER, InfiniteBench
