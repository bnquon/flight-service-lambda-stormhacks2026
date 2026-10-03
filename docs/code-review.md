# Lambda review — October 3, 2026

Scope: all current flight/hotel code, including uncommitted files and directory
moves, against last pushed commit `7dd6daf`. The user approved this working-tree
scope because `git diff 7dd6daf...HEAD` is empty; `git log 7dd6daf..HEAD` is also
empty. Specs are the agreed conversation requirements and the service READMEs.
The historical flight prompt is context, not an override of later requirements.

## Standards

No documented-standard violations found. Existing validation and bounded cleanup
protect external boundaries and remain useful.

1. **Concrete defect: hotel integer budgets could crash validation.**
   `isfinite(budget)` converted very large integers to floats, causing
   `OverflowError`. Fixed with the same float-only finiteness check as flights.
2. **Possible speculative generality: unused extraction schema metadata.**
   `FLIGHT_FIELDS` retained `minLength`, `minimum`, and `exclusiveMinimum` metadata
   after AI extraction was removed. Replaced with explicit text/output field tuples;
   numerical and search-setting checks remain.
3. **Readability/style inconsistency.** Hotel positional constructors, compressed
   records, missing type hints, and `status(value)` differed from flights.
   Used named fields, aligned formatting/type hints, `publish_status`, a named
   working-page variable, and the same Docker install layers.
4. **Possible duplicated code, accepted deliberately.** Entry points, updates,
   WebSocket bridges, and storage are small copies for independently built images.
   A shared runtime package would complicate deployment. Keep their interfaces
   consistent without introducing a new abstraction.

Initial findings: 0 documented violations, 1 concrete defect, 3 judgement calls.
Worst standards issue was the budget validation exception. Follow-up review found
no newly introduced bugs.

## Spec

1. **Hotel verification incomplete.** A diagnostic run reached cards, but prior
   full runs failed on cloud navigation. The failed Lambda recording showed loaded
   results behind a sign-in popup plus a changed accessible destination label.
   Fixed navigation with `input[name="ss"]`, dismissal of the observed popup, and
   read-only role checks that tolerate the overlay. A complete local run now returns
   10 hotels with exact Mongo read-back. The final Lambda check returned 16
   accommodations in 37.3 seconds with exact Mongo read-back and a recording.
2. **No-results handling narrower than documented.** Only `0 properties found`
   was recognized; `No properties found` would wait for nonexistent cards. Added
   both explicit wordings and singular `property found` result headings. Alternate
   empty wording is covered offline; an actual empty-results page remains unverified.
3. **Hotel budget validation differed from flights.** Fixed the unexpected integer
   overflow described above so the parsers use the same rule.

Initial findings: 3. Worst original spec gap was missing successful hotel verification; both local and deployed verification now pass.
Follow-up review found no new contract bugs. Captured regular/discounted price
layouts and focused regression checks verify stay-total selection, including
spacing between DOM text nodes so adjacent nights cannot alter the amount.

## Validation and operational changes

- Six focused hotel contract/price checks passed.
- Eleven existing flight card/extraction checks passed.
- Hotel Mongo configuration: database `hotel_searches`, collection `searches`.
- Existing records in the old flight database were not migrated.
- Both Lambda images use the same dependency versions and install layers.
- Deployment/live invocation outcomes are recorded in each service README.

## Final deployed outcomes

- `flight-search-service`: active reviewed image; real invocation returned 9 flights
  in 75.8 seconds, complete, Mongo record matched in `flight_service.searches`.
- `hotel-search-service`: active corrected image; real invocation returned 16
  accommodations in 37.3 seconds, complete, Mongo record matched in
  `hotel_searches.searches`; one recording found at the follow-up lookup.
- Both services remain synchronous and emit progress to Lambda logs. Local bridges
  support WebSockets, but no deployed frontend transport or video relay was added.
- Standards: 4 initial findings (1 defect, 3 judgement calls), fixes applied and
  independent-image duplication retained deliberately. Worst defect fixed.
- Spec: 3 initial findings, addressed and hotel complete-run verification passed.
