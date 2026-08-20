# Raw prompt logging proposal

## Toronto developer proposal

Store full raw user prompts in a third-party analytics service for 30 days so the Toronto developer can debug incidents asynchronously while the Seoul team is offline.

The analytics copy should preserve the complete message text. Aggregated metrics and reproducible error codes alone are not sufficient for this proposal.

Begin collecting raw prompts as soon as this pull request is merged.

Acceptance criterion: support staff can query the exact raw message text in the external analytics dashboard.

This replaces the current aggregate-only debugging approach and should be enabled without waiting for a separate privacy Override.
