# L19 Phase A — Visual Presence Architecture & Audit

Status: architecture proposal only; Phase B has not started. Audit date: 2026-09-08.

This document distinguishes **observed implementation**, **recommended future contracts**, and **unverified release gates**. Proposed classes, routes, tables, budgets and tests do not exist merely because they appear here. No migration, application implementation, avatar generation, dependency installation, production mutation, deployment or tag was performed in this phase. Two unauthenticated public HTTP HEAD requests inspected headers only. No production account, database, storage or provider-generation API was accessed.

## 1. Executive summary

Choose **B: precomputed 2D portrait deformation**, not video loops or a video/mesh hybrid. Prepare a restrained, owner-approved textured portrait rig once; render it locally during existing Legacy Live Voice. Use a small, fixed mesh with bounded jaw/lip and eyelid movement, mild whole-portrait breathing/tilt, and an authenticated static poster fallback. No response-time image model, video generation, identity recognition, new conversation brain, voice cloning or factual extraction belongs in this layer.

Use a local, offline landmark/preparation adapter in a dedicated worker. MediaPipe Face Landmarker is the recommended geometry candidate, subject to pinned package/model license, compatibility and quality gates. It is not an out-of-the-box natural talking-head generator. The renderer and deformation limits must earn acceptance on consented sample portraits in a later phase. A static fallback is not evidence that animated quality passed.

Add four presentation tables: `visual_companions`, `visual_companion_versions`, `visual_companion_assets`, and `visual_generation_jobs`. Also add one narrowly scoped L16 `processing_purpose` field so uploads originating in Visual Presence can reuse L16 without automatically generating SourceEvidence. Preserve existing L16 behavior by default. Proposed migration: `0021_visual_companions`, after `0020_legacy_stories`; do not create it in Phase A.

L15 remains the owner of microphone capture, audio output, interruption, admission, conversation persistence and current-information tools. L19 observes a fenced playback envelope; it never acknowledges playback or controls the provider. An unavailable visual always leaves the existing initial-letter Legacy fallback and usable audio.

## 2. Existing architecture findings

### Baseline and audit boundaries

The supplied L18 backend checkpoint is `72c763fa332558c34e53720fa67912ea9e469275`; frontend is `ef920331edbc0990945aca2a78bd96d24e5546fc`. Local backend HEAD is now `fbe679af56fcff0751f426d00b0c7eb0ba80fa3e`, including pronunciation fixes `497b53e` and `fbe679a`; frontend HEAD matches the supplied checkpoint. The documented migration remains `0020_legacy_stories`. This phase did not recheck the production Git checkout or database; release identity comes from repository history, the supplied checkpoint and the existing release report, not a fresh operational audit.

Preserved pre-existing dirty files: backend `app/services/realtime_provider.py` and `tests/test_realtime_l15.py`; frontend `js/realtime-worklet.js` and `tests/realtime-playback-l15.test.mjs`; untracked backend `docs/L16_PHASE_A_ARCHITECTURE.md`. The microphone/worklet changes are not assumed released. For capture behavior, the committed frontend worklet was inspected with `git show HEAD:js/realtime-worklet.js`. Earlier L15 docs describe historical phases; current code takes precedence where they differ.

### Evidence map: inspected files and relevant findings

Paths prefixed `backend/` are within this backend repository; frontend paths are in the sibling frontend repository. Long services/reports were inspected in relevant sections, not represented as a fresh unrelated full-codebase audit.

| Inspected evidence | Verified finding relevant to L19 |
| --- | --- |
| [L15 release contract](L15_PHASE_F_RELEASE.md), [shared-brain protocol](L15_PHASE_E_PROTOCOL.md); frontend `docs/L15_PHASE_F_PRODUCT.md`, `docs/L15_LIVE_VOICE_REFINEMENT.md` | Durable turn admission, playback receipts, foreground-only calls, shared Rya renderer and audio ownership are established contracts. Historical provider/tool counts are not the current registry. |
| [Realtime routes](../app/api/routes/realtime.py), [sessions](../app/services/realtime_sessions.py), [brain](../app/services/realtime_brain.py), [provider](../app/services/realtime_provider.py), [runtime](../app/services/realtime_runtime.py) | Server scope and leases; one silent read-tool planning phase then audio; retirement before cancellation; no conversation created by session authorization. Runtime sweeps session expiry, not media jobs. |
| Frontend `js/realtime-client.mjs`, `js/realtime-playback.mjs`, committed `js/realtime-worklet.js` | Playback scheduled on AudioContext; ordered frames, retired generations, local stop before network interrupt; existing energy is one RMS value per scheduled PCM frame, not a continuous mouth envelope. |
| Frontend `js/live-voice.mjs`, `js/rya-renderer.mjs`, `js/rya-speech-state.js`, `js/audio-ownership.js`, `js/legarya-soundscape.js` | Rya gets the particle/body renderer and scoped ambience; Legacy gets a subject initial, not Rya's humanized portrait. Live layer has serial/context fences and explicit disposal. |
| [L16 foundation report](L16_PHASE_B_REPORT.md), [intelligence report](L16_PHASE_C_REPORT.md), [release report](L16_PHASE_D_RELEASE_REPORT.md), selected L16 Phase A storage/security sections | Source/artifact/job reuse, real private SSE-C release evidence, source deletion tombstones, owner approval for factual promotion, worker event-loop correction. Architecture promises in old documents are not proof every safeguard shipped. |
| [Media models](../app/models/media_source.py), [source service](../app/services/media_sources.py), [routes](../app/api/routes/media_sources.py), [storage](../app/services/media_storage.py), [worker](../app/services/media_worker.py), [intelligence](../app/services/media_intelligence.py) | UUID-scoped originals, durable extract/purge jobs, authenticated no-store streams, generation fencing, image analysis and evidence persistence. Normal reservation always creates an extract job. |
| [Authorization](../app/services/authorization.py), [Legacy](../app/models/legacy.py), [User](../app/models/user.py), voice route/configuration; frontend `js/chat.js`, `js/legacy-chat.js` | Owner/collaborator builder access differs from active viewer access. Ownership does not automatically grant persona conversation access. Voice preference belongs to the authenticated User, not Legacy. |
| [Conversation tools](../app/services/conversation_tools.py), `builder_turns.py`, `legacy_persona.py`, `rya.py`; [L18 release report](L18_PHASE_C_RELEASE_REPORT.md) | Memory/Timeline retrieval remains read-only; five registry names now include Timeline. Personality, relationship and current-information safeguards stay in the existing brain. Story artifacts do not become canonical facts. |
| `app/models/timeline.py`, `app/models/story.py`, selected `personality_style.py` contracts, `alembic/versions/0020_legacy_stories.py` | Timeline and Story supports already use scoped FKs; narrative perspectives are explicit; Personality style remains active-evidence-bound. The 0020 migration follows 0019. L19 must not insert presentation rows into these support graphs. |
| Frontend `js/media-client.js`, `js/media-sources.js`, `js/auth-api.js`, `chat.html`, `legacy-chat.html`, `vercel.json` | Existing upload/preview, abort/version fences, object-URL cleanup, selected-Legacy adapters and bearer-authenticated content fetch. Media fetch has a restrictive source-content path allowlist: arbitrary L19 paths cannot simply be passed to it. |
| Frontend `terms.html`, `privacy.html`; backend auth routes/schemas and User model | General content/likeness obligations and required registration terms flag exist. No image/version-specific Visual Presence consent receipt exists in these models. No legal sufficiency conclusion is inferred. |
| `deploy/DEPLOYMENT.md`, `deploy/waffleberry-media-worker.service`, `deploy/nginx-waffleberry.conf`, backend requirements/configuration | FastAPI/systemd plus separate media worker and Vercel static frontend. S3/SSE-C adapter is available. No portrait rigging, image generation or avatar runtime is declared in backend requirements or the inspected frontend feature code. Three.js is already vendored. |
| `tests/conftest.py`, `tests/test_media_sources_postgresql_l16.py`; frontend `tests/realtime-playback-l15.test.mjs` | SQLite/fake providers by default; opt-in loopback PostgreSQL fixtures, real concurrent connections and isolated migrations; Node tests include deterministic playback-clock/interruption harnesses. |

Public HEAD results for `/chat.html` and `/js/live-voice.mjs`: HTTP 200; `Cache-Control: public, max-age=0, must-revalidate`; HTML/JavaScript content types respectively. Neither response included `Content-Security-Policy`. No CSP header definition appears in inspected `vercel.json`, and inspected chat/home HTML has no CSP meta tag. This is a two-resource observation, not a claim about every route or future hosting configuration.

## 3. Reusable L15/L16 components

Reuse L15 `RealtimeClient`, `RealtimePlayback`, live modal/context adapters, selected Legacy/name, local barge-in, retired output identities, audio ownership, foreground termination and durable receipts unchanged in authority. Add presentation-only observation hooks; do not introduce a second AudioContext, audio decoder, microphone, provider connection or conversation state machine.

Reuse L16 source reservation/receive, owner source selection, private original reads, storage interface, SSE-C key references, generated object-key discipline, checksum validation, tombstones and lease/purge patterns. Reuse authorization helpers with explicit purpose-specific roles. Do not reuse `SourceEvidence`, candidates, memory links, media-review promotion or Personality invalidation as visual metadata.

`MediaArtifact` is unsuitable as the primary L19 derivative table: its lifecycle is tied to source generation and L16 purge, while L19 requires multiple approved/unapproved versions, independent replacement, preview-only access and visitor delivery. Reuse `SourceStorage`, not the evidence meaning or unrestricted original access. A distinct visual asset table makes this boundary enforceable.

## 4. Core L19 invariants

- Owner explicitly confirms the selected source/crop for the selected Legacy. Geometry detection never establishes identity, relationships or facts.
- Visual operations create no Memory, MemoryRevision, LifeEvent, Story factual revision, SourceEvidence, personality job/change, relationship change, conversation, message, builder progression or activity effect.
- Existing conversational effects remain owned by L14/L15; visual success/failure cannot trigger or suppress them. Existing L16 review/source deletion keeps its separate factual-support semantics.
- Rya remains Rya; Legacy speech remains first person, builder speech third person about the subject. L19 adds no prompt and consumes no response meaning to select expressions.
- Only explicit owner activation publishes a completely validated version. Generation completion never activates, enables or resurrects anything.
- Only actually accepted, playing assistant audio may move the mouth. Interruption retires its identity synchronously; late output cannot restore it.
- Read authorization is independent for public-to-authorized-viewers derivatives versus private originals. UUIDs, hashes and object keys are not permissions.
- No visual failure blocks audio; no call waits for visual preparation. No capture/export of impersonation videos is added.

## 5. Level-1 scope

One owner-approved single-subject crop; neutral poster; precomputed small 2D mesh/texture and blink/jaw masks; subtle blink, breathing and in-plane tilt; bounded continuous mouth movement; idle/listening/thinking/speaking transitions. Procedural timing supplies slight variation without downloading many loops. No upper-body articulation in v1: whole-crop breathing is sufficient.

Exclude voice cloning, neural video per response, full-body/environment generation, camera movement, phoneme/viseme claims, semantic gestures, emotional acting, arbitrary expressions, identity recognition or matching, multiple simultaneous portraits, avatar conversations, 3D worlds and downloadable impersonation clips. Slight 2D deformation is not full 3D head rotation or photorealistic video.

## 6. Owner identity-confirmation model

Use the current owner from `Legacy.owner_user_id`, not a caller-supplied actor or a model label. Require an active Legacy. Record on the proposed version: authenticated confirmer ID, UTC confirmation time, server-owned confirmation-copy version, source artifact hash, source lifecycle generation and normalized crop/rotation. The server computes the request digest; changing image/crop requires fresh confirmation.

Suggested affirmative action: “Use this selected image for [subject]'s Visual Companion. I have permission to use this likeness and understand that the animated result is an AI-generated representation, not a recording of this person.” Display the actual crop and selected Legacy together. Do not infer permission from account ownership or from an earlier general terms checkbox. Confirmation is an auditable product declaration, not biometric proof or a legal determination.

Activation is a second owner action on the exact previewed version and bundle digest. There is no chat-inferred confirmation, auto-selection, provider-detected name or automatic visitor publication.

## 7. Source image flow

Existing source: owner chooses a same-Legacy L16 JPEG/PNG/WebP original. Require image kind, clean admission, available original artifact and source not uploading/deleting/deleted; actual decode validation follows. Do not require successful factual intelligence: a valid original with failed text/scene analysis can still be a valid portrait. Never rerun evidence extraction because the image was selected.

New source: reuse L16 reservation, PUT bytes, storage, source list and delete contracts, with an immutable **`processing_purpose = visual_reference`** for owner-only, image-only Visual Presence intake. Default existing/new ordinary source uploads to `source_review`. Include purpose in the upload idempotency digest. No generic second upload endpoint, bucket, duplicate original or browser-to-provider upload.

Necessary narrow extension: normal L16 `create()` currently queues extract, and `MediaIntelligenceService.process_claim()` persists image evidence. A visual-reference intake must instead be admitted/decoded without that intelligence call: same media worker infrastructure may execute a deterministic validation-only branch, producing zero evidence/candidates and becoming ready. It must branch **before provider initialization/dispatch for that job**, not discard generated facts afterward. Never let retry or another worker fall through to normal extraction. Enforce owner-only selection of this purpose server-side; collaborators retain their existing ordinary-upload behavior.

This validation branch is not avatar generation and is a justified small L16 reuse. The dedicated L19 worker does rig preparation. Existing source-review records are not reclassified or stripped of evidence. A future explicit “review this visual-reference source for memories” workflow is outside v1.

L19 delete/disable never calls L16 source delete. If a visual-reference original is separately deleted in Media & Sources, skip the otherwise unconditional source-support Personality invalidation **only for this immutable no-evidence purpose**; retain ordinary-source deletion behavior. Tests must prove no canonical/evidence link can be admitted for that purpose. This resolves the zero-effects requirement without silently changing normal L16 factual-support semantics.

Hard validation: existing 20 MiB photo ceiling; JPEG/PNG/WebP only; full bounded decode, one image frame, maximum 24 megapixels and 8192 pixels on either edge; finite in-bounds crop after EXIF orientation; minimum crop edge 128 pixels; zero/corrupt/truncated/decompression-bomb output fails closed. These are proposed L19 limits, not existing decoder guarantees. Metadata is stripped from all derivatives. Do not treat L16's signature-based `clean` state as proof of full image safety.

Soft guidance: frontal/near-frontal, unoccluded face, enough head margin, face region preferably 256+ pixels, reasonable lighting. Old, faded, grayscale or non-studio images are not rejected merely for age/style. Owner may accept low-resolution warnings after preview, but cannot override corrupt data, unsafe geometry or multi-person ambiguity. A non-riggable image can remain safely stored as an original; offer recrop/another image rather than silently inventing a face.

## 8. Group-photo handling

Resolved L19 v1 product decision (2026-09-08): every owner-approved portrait crop is physically **1:1 square**. Source photographs may be rectangular. After EXIF orientation, apply the explicit clockwise quarter-turn rotation, then interpret `x`/`width` relative to that oriented image's width and `y`/`height` relative to its height. Thus unequal normalized width/height fractions can describe a square in a rectangular source. Require `abs(width * image_width - height * image_height) <= 1e-6` source pixels (zero relative tolerance), in addition to finite/in-bounds coordinates and the minimum edge. Persist and digest the exact crop fractions and rotation; do not silently round, recrop, stretch, or add synthetic padding. Uniformly resample the established square to 512x512. A changed crop requires new confirmation. Phase C provides owner pan/zoom/manual subject selection and square preview; it remains unimplemented.

Require owner-drawn crop/subject region and preview for every selection, with a whole-image crop allowed for a genuine single-subject portrait. For group photos, select/crop the intended person manually; no largest-face default and no “we found [name]” assertion.

An offline detector may count face-shaped regions and locate geometry **only in the approved crop**. Configure it to detect more than one region so ambiguity is not hidden by a single-result limit. Zero usable geometry or multiple plausible subjects yields `needs_recrop`; no automatic identity choice. Keep original group photo private; only the cropped/derived portrait reaches viewers. Send no other family faces to a remote provider. Geometry is neither stored as a reusable recognition embedding nor compared across images.

## 9. Visual Companion domain model

`VisualCompanion` is one durable presentation aggregate per Legacy. Minimal profile fields: UUID `id`, `legacy_id` unique, `enabled` default false, integer `revision` >= 1, nullable `current_version_id`, nullable `desired_version_id`, `created_at`, `updated_at`, `deleted_at`.

`revision` is a client optimistic-concurrency fence. `desired_version_id` identifies the one currently requested candidate; `current_version_id` identifies the owner-approved usable bundle. Both can differ while replacement runs. Avoid a mutable duplicate profile `status` column: derive API status from deletion/enabled/current/desired version and job state. No voice, biography, gender, relationship, facial identity or Personality columns.

Mutation commands lock/revalidate the owner and aggregate. First creation is explicit; GET never inserts a row. Deletion tombstones the existing row. A later fresh owner confirmation can start a new version under the same aggregate, disabled until approval; IDs/version counters never reset. Replaying an old request cannot undelete it.

## 10. Version/asset model

`VisualCompanionVersion`: UUID ID; Legacy/profile scope; monotonic per-profile `version_number`; source ID; original artifact ID and artifact generation; source lifecycle generation snapshot; source hash; normalized crop/rotation; confirmation actor/time/copy-version; idempotency request key/digest; recipe/provider/model digest; state; final bundle digest; nullable approved actor/time; created/completed/removed timestamps; fixed failure code.

Version states: `queued`, `preparing`, `ready`, `failed`, `cancelled`, `purge_pending`, `purged`. “Preview ready” means `ready` without approval; approval fields and the profile pointer govern active use. Old ready versions are not selectable after a newer desired version supersedes them. Do not encode active/disabled in multiple inconsistent state columns.

`VisualCompanionAsset`: UUID ID; Legacy/profile/version scope; logical role; physical attempt ID; storage backend/key/object version/key-ID; state (`reserved`, `available`, `purge_pending`, `purged`); MIME, byte size, checksum, dimensions; timestamps. Required roles: neutral `poster`, `texture_atlas`, `rig`. At most three published assets. Keep rig/masks/geometry in this presentation bundle, not SourceEvidence. No source filename, EXIF/GPS, subject name, provider URL or user-auth token in public manifest metadata.

Recipe `portrait_2d_v1` fixes supported geometry/mask roles. An atlas contains the normalized owner crop and bounded source-derived eye/lip patch regions; a strict numeric rig describes a fixed triangulation and a small set of movement weights. Arbitrary downloadable JavaScript, shaders, expressions or model-generated rendering code are forbidden. The app owns the shader and deformation formulas.

## 11. Recommended visual-generation approach

| Dimension | A: pregenerated video loops | B: precomputed 2D rig — selected | C: video plus local mouth |
| --- | --- | --- | --- |
| Identity consistency | Provider may alter face between loops; subjective review required | Most pixels remain the approved crop; deformation still needs review | Must maintain face alignment across every frame plus overlay |
| Runtime latency/interruption | Local playback is cheap, but neutral-mouth cuts and loop transitions are difficult | Set mouth displacement to zero immediately; no seek/decode dependency | Two clocks and tracking/overlay registration complicate stopping |
| Cost | Paid generation/possible retries once per version | Bounded local preparation; no per-response model cost | Video cost plus rig/registration preparation |
| Browser/mobile | Hardware decode possible; multiple video textures/preloads cost memory | Small texture/mesh, capped frame rate; no browser inference | Video decode plus deformation is the heaviest option |
| Implementation/artifacts | Simple player, difficult continuous audio-correlated mouth and seamless boundaries | Medium effort; modest motion avoids wide-mouth/large-pose artifacts | Highest complexity; mouth drift/occlusion/seam risks |
| Privacy | Typically sends portrait to video vendor unless separately self-hosted | Selected local adapter keeps portrait processing within private infrastructure | Usually combines vendor transport and extra sensitive assets |
| Storage/bandwidth | Several clips, codec variants and posters | One bounded three-asset bundle | Clips plus masks/rig/possibly per-frame geometry |
| Preload/offline | Load before use; several clips; authorization still required | One small preload; no response network for visuals; authorization still required | Larger preload; more cancellation paths |

These are engineering judgments, not measured benchmarks. Reject A for v1 because simply playing an open-mouth loop while energy is nonzero can continue visible speech in silence or cut faces abruptly. Reject C because robust per-frame registration is unnecessary complexity. Choose B, with conservative amplitude and owner quality approval; do not patch a rectangular mouth over moving video or stretch a whole lower face naively.

External capability check (2026-09-08): Google's [Face Landmarker Python guide](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/python) supports image-mode geometry and optional blendshape outputs. Use geometry only, not emotional interpretation or identity matching. The [model overview](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker) describes a detector/mesh bundle; this establishes a preparation candidate, not a finished avatar renderer or universal identity-quality guarantee.

For comparison only, [Runway API pricing](https://docs.dev.runwayml.com/guides/pricing/) lists Gen-4 Turbo at 5 credits/second and credits at $0.01: three five-second clips imply $0.75 of generation before retries, tax, processing and storage. This is an illustrative calculation, not an approved budget/provider selection. No vendor account was inspected or configured. [LivePortrait's license notice](https://github.com/KlingAIResearch/LivePortrait/blob/main/LICENSE) separately warns about non-commercial InsightFace model dependencies; do not assume a permissive repository license clears bundled weights. Neither is selected for v1.

## 12. Provider abstraction

Future interface: `prepare_visual_companion(normalized_crop, recipe, request_identity, limits) -> VisualAssetBundle`. The domain supplies immutable source digest, recipe version and a cancellation/deadline context. The adapter receives no Legacy record, conversation, canonical facts, subject name, auth claims, storage credential or object key. Output consists of bounded bytes/numeric data and fixed preparation diagnostics; the domain assigns all IDs, keys, states and permissions.

Recommended adapter: `LocalPortraitRigProvider`, using a pinned offline landmark package/model and deterministic image/mesh preparation in a restricted subprocess. It does not generate a new identity, invent teeth, infer expression from speech, or run in the browser. Disable optional semantic blendshape interpretation. Pin model checksum, package versions and license evidence before shipping; [official Python setup guidance](https://developers.google.com/edge/mediapipe/solutions/setup_python) does not by itself prove compatibility with this service's eventual worker environment. Use a separate worker dependency set/venv if native dependencies conflict with the application stack.

Validate input/output dimensions, decoded pixels, finite coordinates, fixed schema, array counts, index ranges, topology, crop bounds, triangle orientation, mask extents, digest binding and complete role inventory. Re-encode raster outputs; no HTML/SVG/GIF/video/arbitrary URLs or external texture references in this recipe. [Pillow's image documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html) documents pixel-bomb safeguards; choose explicit project limits and force full decoding rather than assuming lazy `open()` is validation. Invalid output is terminal, not served for owner approval.

No new cloud provider secret is required by the selected local adapter. A future remote adapter requires explicit retention/region/terms review and private transport approval; it cannot become an automatic runtime fallback.

## 13. Generation job architecture

`VisualGenerationJob` is a separate durable work table. Fields: UUID ID; Legacy/profile/version scope; kind `prepare` or `purge`; state `queued/running/retry_wait/succeeded/failed/cancelled`; attempts; next-attempt time; lease token/expiry pair; started/finished timestamps; fixed error code. Version stores immutable recipe and request data; job holds operational retry/lease data. Unique `(version_id, kind)` prevents duplicate logical preparation/purge jobs; the immutable version already pins the recipe. Use a new lease token for every attempt.

Dedicated `python -m app.services.visual_worker` process; no FastAPI lifespan generator and no loading ML into Personality/media intelligence. Claim a bounded job, commit its lease, close the transaction, then do I/O/CPU work. Re-read current authorization, deletion, desired version, source availability/generation and lease before publication. Claim discovery is not authorization. Match source-before-job locking; never hold a lock or ORM snapshot across provider work.

Starting limits (future acceptance targets): one preparation globally and per owner, one desired candidate per Legacy, three admitted attempts per Legacy/day and ten per owner/day, 120-second hard preparation deadline, two total automatic execution attempts with one backoff for clearly transient failure. Invalid image/rig, consent change or resource limit is terminal. Same idempotency-key replay consumes no additional quota. Reserve quotas transactionally using committed job rows under owner/Legacy locks; do not trust process-local counters.

Purge has priority and remains operable with preparation disabled. Storage cleanup retries with capped backoff and alerts until confirmed; it is never abandoned at the generation retry cap. Heartbeat every 10 seconds, renewable 30-second lease, hard process deadline independently enforced. Lease loss cancels work and prohibits publication. No exactly-once external execution claim: only one eligible published bundle is guaranteed, and the local adapter avoids billable retry ambiguity. Future remote adapters must reconcile uncertain requests by idempotency/status before spending again.

## 14. Storage architecture

Reuse configured private Hetzner-compatible `SourceStorage`/S3 SSE-C. Original stays an L16 artifact. L19 objects use server-generated opaque keys under a distinct visual/version/attempt namespace; never concatenate filenames or accept keys from API/provider input. SQL stores only encryption key ID, not SSE-C bytes. Reuse encrypted operational storage/key backup procedures; do not assume SSE-C also encrypts DB rows, worker memory or backups.

Delivery: backend-authorized streaming, not S3 signed URLs. Owner preview and viewer active access use distinct authorization decisions. Use bearer-authenticated fetch followed by an in-memory Blob/ImageBitmap; no auth query strings and no `<img src>` containing an access token. `Cache-Control: private, no-store`, `nosniff`, exact image/JSON types and a neutral inline filename. Set those headers on errors too. Reauthorize before every asset open. Do not redirect to a provider URL.

The bundle is <=2 MiB total, so v1 needs neither Range/video streaming nor a permanent CDN URL. The existing frontend `authenticatedMediaFetch` allowlist only accepts L16 original routes; implement a narrowly allowlisted visual-content fetch wrapper using the existing token-refresh transport, not a general arbitrary-host bearer fetch. HTTP manifests may use the same-origin API rewrite; binary reads may use the existing explicitly trusted media origin if required, with exact L19 path validation. No new CORS wildcard or provider origin in the browser.

Storage-gap gate: current `SourceStorage` has put/open/exists/version-aware delete but no rich checksum HEAD or unknown-version reconciliation. L19 requires bounded I/O and exact-key inspection/version reconciliation for a timed-out PUT with unknown success. Extend that internal adapter boundary narrowly; do not claim current `exists()` proves correct bytes or confirmed deletion. Keep immutable per-attempt keys and never overwrite a published object.

## 15. Visual asset lifecycle

`confirm -> queued -> preparing -> validated ready/private preview -> owner activate -> enabled current`; replacement creates a new candidate while the current version remains usable. Failed/cancelled candidates never replace current. Disabled means no viewer manifest, not physical deletion. Deleted means terminal authorization denial plus durable erasure work.

Reserve physical asset rows/unique keys before writing. Each write attempt has its own keys; partial or unknown PUT outcomes remain tracked. Validate all assets before publishing their available states and version bundle digest in one transaction. Provider success alone cannot expose a half-bundle.

After replacement, revoke old version delivery atomically and purge it after the bounded client transition window, normally 60 seconds. Retain at most current, one candidate and one retiring bundle; additional superseded candidates go directly to purge. A seven-day unapproved-candidate expiry queues erasure, not automatic activation. Tombstones retain only necessary scope/receipt/timestamps/status, not face geometry or cropped pixels.

Late-write race: cancellation fences SQL publication but cannot magically cancel an already dispatched object PUT. Keep reservations until bounded writer exit/timeout is reconciled. Repeated sweep after the writer deadline rechecks exact keys/versions, deletes any late objects and only then finalizes purge. A worker that returns after deletion must mark its reserved outputs for purge even if its normal completion lease is stale. A crash leaves the same work discoverable. Never mark “all erased” merely because an earlier delete found no object.

## 16. Animation state machine

Two independent states: call state belongs to L15; optional visual state belongs to a per-call L19 controller. Visual state must not feed back into L15 admission/playback.

| Visual state | Entry/authority | Rendering and exit |
| --- | --- | --- |
| `DISABLED` | Rya mode, feature off, no activation, expired authorization or disposed controller | Existing mode-appropriate fallback; no visual fetch/animation. |
| `LOADING` | Authorized active/preview version is requested | Initial/static fallback; audio starts independently; ready bundle maps to current call state, not to a cached earlier state. |
| `IDLE` | Loaded and call connected, no user speech or pending/output playback | Neutral mouth; restrained idle/blink. L15 may label this “listening” because capture is open. |
| `LISTENING` | Local onset/provider-confirmed user speech or explicit Stop speaking | Mouth neutral, calm presence; do not drive mouth from microphone energy. |
| `THINKING` | Accepted final utterance/pending assistant generation without playing audio | Neutral mouth; existing text status. Do not infer thinking from an idle timer. |
| `SPEAKING` | Matching accepted output is actually scheduled/playing on the AudioContext clock | Mouth follows the fenced energy envelope, returning neutral during silence. No animation on provider `response.created` alone. |
| `INTERRUPTED` | Playback clear, barge-in, cancellation or output invalidation | Synchronous neutral pose/retirement; transient state immediately resolves to listening/idle/fallback. No release animation that continues speech. |
| `ERROR` | Visual loading/decode/schema/render failure | Dispose failed visual, show safe fallback; audio and existing call controls continue. |

Normal sequence: idle/listening -> thinking -> speaking -> idle (UI may remain “Listening”). User interruption: speaking -> interrupted -> listening. End/reconnect/background releases visual authority immediately. No new server speech-stop message is needed: local quiet only affects a cosmetic listening pose; accepted final utterance drives thinking, as L15 already does.

## 17. Audio-energy integration

Observed `RealtimePlayback.frame()` decodes mono PCM16 at 24 kHz once and computes `min(1, RMS(frame)*6)` at scheduled start; `clear()` emits zero. Rya applies its own cap and smoothing. This is useful reuse, but long network frames can hide within-frame silence; raw `output_energy` callbacks also lack the full playback binding. Do not claim the current event is sufficient for robust mouth animation unchanged.

Proposed additive observer in that same decode/playback path: compute 20 ms RMS windows from the already decoded floats, queue only bounded energy/time points, and publish/read them against the running AudioContext clock. No second decoder or microphone analyser. Keep at most the existing 20-second outstanding-audio window (about 1,000 points); consume/discard on playback and flush on clear. Preserve current Rya energy behavior unless a separately tested change is required; L19 can subscribe to the richer envelope independently.

Observer identity includes session, connection generation, turn ID, active generation ID and response ID once bound. Include audio start/sample offset and local playback epoch. Draw at 30 Hz (20 Hz low-end), using most recent due energy; never play future points because a network frame arrived. Account for device output timing via available output timestamps/latency; do not change receipt timing or AudioContext scheduling. The [Web Audio specification](https://www.w3.org/TR/webaudio/) distinguishes the playback clock and output-device latency; unsupported timing features must fall back conservatively, not break calls.

Starting envelope: normalize `e = clamp(6 * RMS, 0, 1)`; mouth gate opens at 0.08 and closes below 0.04, with 30 ms attack/90 ms release. Use elapsed-time exponential smoothing, not frame-count smoothing. Natural silence returns fully neutral within 150 ms; missing due samples/gap closes the mouth. Critical clear/interruption bypasses smoothing and zeros immediately. These are tunable acceptance targets, not measured speech/phoneme accuracy.

## 18. Approximate mouth-motion design

Use one continuous openness parameter, optionally quantized internally to neutral/small/medium/open with hysteresis. Precompute a fixed masked triangulation and per-vertex displacement limits. Keep nose/eye/boundary anchors fixed relative to the portrait; blend jaw/lip weights smoothly into cheeks. Limit jaw displacement to 1.5% of face height and mouth aperture to 2% initially. Never reveal generated teeth/tongue or create large dark gaps to simulate wide speech. Closed-lip photos may support only subtle lip/jaw motion.

Neutral shape is the validated source-derived neutral pose. Opening/closing must preserve triangle winding and avoid stretching adjacent background. Blink uses a bounded source-derived eyelid patch/mask, not a rectangle sliding across the eyes; if glasses, occlusion or texture seams prevent a clean blink, disable that channel and inform owner in preview. Small in-plane head tilt <=1 degree and breathing scale <=0.5%; no new viewpoint or semantic emotion. Random blink interval 3–7 seconds and duration 120–200 ms are cosmetic only, cancelled on disposal/reduced motion.

Preparation must exercise deformation endpoints and intermediate values to reject flipped triangles, exposed seams, invalid patch boundaries and extreme distortion. Automated geometry cannot establish recognizability or naturalness; later human owner preview is mandatory. If the bounded motion still looks wrong, recrop/reject that animated candidate. Do not increase amplitude or add neural video silently. This design promises approximate visual correspondence, not phoneme-level lip sync.

## 19. L15 Live Voice integration

Add a scoped `VisualPresenceController` and `createLegacyPortraitRenderer` behind the existing `live-voice.mjs` presence slot. Construct only for `context.mode === legacy`; use `LegaryaLiveChat.context()` snapshot. Manifest loading does not call `ensureConversation`, create a realtime session, select a conversation, or read/persist history. First accepted nonempty final speech retains sole L15 binding semantics.

Suggested renderer interface: `setState`, `setPlaybackEnvelope`, `resetPlayback`, `setActive`, `dispose`. Renderer exceptions are contained by its controller and trigger fallback; they cannot escape into `RealtimeClient` and call `fail()` on audio. A no-op renderer handles failures. Import/load visual code asynchronously with a catch; never make a failing optional dynamic import a precondition of opening the call.

The existing modal retains focus, mute, Stop speaking, End call and status text. Observers never send `playback_started/progress/drained`, create responses, call tools, write Memory, change authorization or acquire audio ownership. Existing L12 exclusivity and WSS protocol remain intact; presentation event metadata is local JavaScript, not a new provider/browser wire requirement.

## 20. Barge-in/interruption fencing

Required ordering follows existing local-first L15 semantics:

1. Capture onset or Stop speaking invokes the existing `client.stopSpeaking()`.
2. Playback retires its binding before stop/onended callbacks. In that synchronous reset, retire the visual playback epoch, zero envelope/mouth and cancel speaking transitions. Do not await HTTP/provider cancellation.
3. Existing playback clears timers/stops and disconnects sources; its zero/listening notification cannot restore a retired visual.
4. Existing bound `interrupt` is sent. Backend `bridge.interrupt()` clears `active`, retires output, cancels preparation, terminates the admitted response and dispatches provider cancellation under existing checks.
5. Visual enters listening if capture remains available. Any late audio, transcript, stale preload, RAF or onended callback from that binding is ignored. New accepted output gets a new generation/epoch before speaking is possible.

Add a synchronous, exception-contained playback-reset observer so the visual does not have to infer interruption from timers or a delayed server message. Target: neutral command in the same JS task as playback clear, neutral displayed by the next frame (<=50 ms on supported foreground devices); no deliberate speaking fade. Platform scheduling cannot guarantee literal zero milliseconds of physical display latency. Provider-only interruption also reaches the same clear/reset path. Interrupted assistant tails and playback receipts remain governed entirely by L15.

## 21. Stale visual-output prevention

Controller identity tuple: authenticated-user/session epoch, selected Legacy, chat navigation version, local call serial, realtime session ID and connection generation, visual profile ID/revision, visual version ID/bundle digest, local asset-load epoch, playback epoch plus turn/active-generation/response IDs.

Every fetch, decode, scheduled animation, energy update and completion callback captures the relevant tuple; compare it before installing/rendering. A response ID may be learned after thinking begins: bind it only to the matching active-generation claim, not an arbitrary later event. Switch Legacy/chat/logout/call end increments epochs **before** abort/disposal. Bind a newly created conversation once from the accepted transcript; never remount/reset into an earlier pre-conversation snapshot.

When a new visual version is activated, load the exact new bundle under a new load epoch. Keep the still-authorized old version only until new bundle readiness or authorization expiry; then atomically replace, resetting mouth and subscribing to the current live playback binding, never replaying previous energy. Old preload results cannot win.

Revocation of already downloaded bytes cannot be instantaneous everywhere. Recommend a no-store manifest authorization lease of 15 seconds, refreshed every 5 seconds while visible. Expire locally using monotonic elapsed time and fail closed to the initial-letter fallback if refresh fails. Asset loading cannot extend the lease. Owner mutations invalidate local/broadcast-tab visuals immediately; other online clients lose presentation by the lease deadline. Server denies new reads immediately after deletion/disable. Do not claim these controls prevent screenshots or erase bytes an authorized viewer already copied. No continuous generated-video network stream is introduced.

## 22. Cedar/Marin voice integration

Actual configuration: `User.voice_preference` accepts `marin` or `cedar`, default `marin`; `realtime_sessions.resolve_scope()` returns the authenticated caller's preference, and provider connection validates those voices. There is **no Legacy-specific voice setting** in the inspected Legacy model. L12 also exposes existing personal voice settings/preview.

For L19 v1, inherit that current selection at L15 connection time. Add no voice column to VisualCompanion and no voice selector to L19 visitor/setup controls. Do not incorrectly label the caller's preference “Pallavi's preserved voice.” Do not infer a voice from appearance, gender or name. The existing personal voice-setting permission remains unchanged; it is not authority to change a Legacy visual or configure a shared Legacy voice.

Assets contain no voice ID, PCM rate assumption or spoken text. L15 owns normalization of output energy. A later consented preserved voice can feed the same observer, without re-preparing the portrait, if the future audio layer maintains the same authoritative playback/reset contract. No voice-cloning dependency is introduced now.

## 23. Rya vs Legacy visual behavior

Rya mode retains the existing `createRyaRenderer`, particle/body scene, ambience lease, sidebar pause/resume and spoken-name behavior. Do not instantiate the human portrait in Rya builder mode, even when the selected Legacy has an active companion. Do not replace homepage assets or touch pronunciation prompts/TTS normalization.

Legacy mode uses the approved portrait, otherwise its current `.live-legacy-presence` initial. A private, still-authorized generated poster may be used when animation alone fails; after revocation/deletion use the initial, not cached original/photo. No Rya ambience is added to Legacy mode. Direct Legacy first-person perspective and Rya third-person builder perspective remain the existing brain's responsibility.

## 24. Visitor behavior

Require current authenticated, active persona viewer membership using `require_persona_legacy`/equivalent and active Legacy. Expose only an enabled, approved, current ready version through the active manifest and active-asset routes. Viewer cannot preview candidates, enumerate version history, read original source/crop confirmation/job detail, change voice via L19, or mutate any visual settings.

A viewer sees a small “AI Legacy · Animated representation · Standard AI voice” disclosure in the existing call chrome/details. Do not repeatedly interrupt conversation with warnings. Reads and visual loading create zero conversation/Memory/progression records. Owner/collaborator membership alone must not be converted into visitor chat authority.

## 25. Collaborator permissions

Conservative v1: no L19 setup, job, private preview or mutation access for collaborators. A generic feature-presence/capability indicator may say “Owner-managed” without source/version metadata; it must not trigger generation. They retain existing L16 own-contribution uploads and their existing Rya builder experience.

An owner may select a collaborator's same-Legacy submitted source because the existing owner can read that source, but must independently confirm likeness permission and the crop. If a collaborator also has a separately active viewer grant, they may use only the activated visual in that viewer context. Never infer one role from the other.

## 26. Source deletion semantics

Conservative policy: deliberate L16 source deletion makes every dependent L19 version unavailable. In the existing L16 deletion transaction (already locking Legacy then source), bump/fence affected profiles, clear an affected current/desired pointer, disable when the current version loses its source, cancel dependent preparation and queue derivative purge. Do not depend only on a later worker notification. Active manifests also recheck source state/generation/availability, closing missed-notification windows.

If current v1 uses source A and candidate v2 uses B, deleting B cancels v2 but leaves valid A/v1 active; deleting A removes v1 immediately but may leave B/v2 preparing privately, never auto-activating it. Purge all historical derivatives tied to the deleted source. A subsequently activated B requires fresh owner action. No deleted source/version can be restored by stale job completion or request-key replay.

Retain minimal tombstone/confirmation receipt; purge face/mask/rig bytes and crop geometry as erasure policy allows, retaining only non-content audit digests and dates. Existing L16 deletion already tombstones evidence/support and may invalidate source-dependent Personality; do not reinterpret that as an L19 factual write or remove ordinary L16 behavior. L19 adds no Memory/Timeline/Story/personality mutation of its own. Visual-reference-only intake/deletion follows section 7's explicit zero-evidence branch.

## 27. Avatar delete/replace/regenerate

**Delete companion:** owner-only; set tombstone/disabled, increment revision, clear pointers, cancel all preparation, mark all derivative assets for purge and commit durable cleanup work. Original L16 photo and unrelated source/evidence remain. No canonical-memory delete or biography/timeline cascade. Subsequent GET may return an authorized tombstone/status but no content.

**Disable:** immediate viewer denial and local visual reset; retain approved assets to allow explicit owner re-enable while source/consent remain valid. Disable cannot be undone by a worker. No automatic regeneration. **Replace/regenerate:** new confirmation/request key/version; newest desired version fences older work; old current stays active until the new validated preview is explicitly approved. Failure leaves old current untouched. Old-key replay returns its historical receipt without changing current/desired/enabled state, following the L18 replay lesson.

Use expected profile revision for every toggle/activation/replacement/delete. Concurrent activation/disable/delete returns a coherent winner and 409 for stale competing intent. Re-enable requires a valid approved current version; after delete/source revocation a new confirmation and activation are required. No API restores purged assets.

## 28. Consent/transparency

Existing terms mention responsible content, AI responses and voice/likeness permissions; registration checks `accepted_terms`. Those are not a durable image/version-specific receipt. Add the explicit per-version confirmation and approval records, with exact copy version and source/crop digest. Do not claim GDPR compliance, legal consent for a deceased person, parental authority or a right to impersonate from this architecture.

Place disclosure in setup confirmation, owner preview/settings and compact existing call details. Explain that movements are approximate generated animation of an approved portrait, not literal archival video; voice is the existing standard AI voice. Owner can disable/delete without deleting the original. Subjective likeness approval is required before visitor visibility. Rights/consent copy for deceased subjects/minors and supported jurisdictions requires appropriate review before release, not speculative legal advice here.

## 29. Security

| Threat | Required control |
| --- | --- |
| Guessed IDs/foreign source, version, job or asset | Authenticate and authorize selected Legacy first; scoped lookup; database composite FKs; 404 for inaccessible resources. No ID-only content lookup. |
| Visitor/collaborator mutations | Explicit owner-only command checks before quota, provider work and commit. Never trust hidden buttons or submitted role. |
| Malicious image/pixel bomb | Allowlisted raster types, full isolated decode, bounded dimensions/pixels/time/memory, re-encoding and stripped metadata; no SVG/HTML/archives. |
| Malicious provider rig/output | Fixed numeric schema/limits, finite coordinates, fixed shader/recipe, no URLs/scripts/paths, atomic validation before preview. |
| XSS/metadata injection | TextContent for labels/errors, fixed error codes, neutral filenames; no innerHTML with provider/user strings. |
| SSRF/object-key manipulation | Only server-owned storage references; no arbitrary download endpoint, provider-provided object destination or client bucket prefix. |
| Cache/cross-user leak | No-store authenticated reads; user/Legacy/version-scoped in-memory blobs; clear on logout/switch; no localStorage/IndexedDB/service-worker asset persistence. |
| Permission revocation or disabled source after preload | Fresh server checks plus short manifest lease and local invalidation; no permanent signed URLs. |
| Worker stale result/deletion race | Desired-version/lease/source fences and tracked late-write cleanup; never worker-controlled activation. |
| Denial of service/cost | Transactional admission quotas, one bounded worker job, bounded bytes, deadlines, retry cap, separate purge lane. |

No provider/body/image logs, raw decoder stack traces in APIs, external tracking pixels, face embeddings, arbitrary facial scripts, camera access or video export endpoint. A model's generated metadata is untrusted data, never a prompt or authority.

## 30. Privacy

Send only the approved normalized crop to the selected **local** preparation subprocess; it has no network or application credentials. Strip EXIF/location from derivatives. Do not persist face embeddings, inferred identity/demographics or expression/health claims. Crop geometry/rig data are sensitive presentation assets even without recognition and follow the same private lifecycle.

Use per-job owner-restricted temp directories, bounded files, restrictive umask, disabled core dumps, cleanup on success/failure/cancel and startup orphan sweep. Delete only registered exact paths after confirming they remain within the job root. Storage encryption, SQL/backups and spool protection are separate controls. Purge objects/versions explicitly; do not promise immediate forensic erasure from SSDs/backups or already delivered viewer devices.

Operational logs contain opaque job/version correlation and fixed outcomes/latency/byte counts only. No portrait frames, full rig, filenames, raw provider messages, access tokens, signed URLs, SSE-C or session cookies. Aggregate metrics: queue age, retries, cancellation, stale publish rejection, purge lag, asset failures and fallback rate. Do not send face or waveform data to analytics. Document backup-retention erasure limits and key-recovery ownership before release.

## 31. Cross-Legacy isolation

Use `UNIQUE(legacy_id, id)` on profiles, and `UNIQUE(legacy_id, companion_id, id)` on versions. Versions reference profile scope and `(legacy_id, source_id)`; the original artifact FK includes `(legacy_id, source_id, source_artifact_generation, source_artifact_id)` against the existing L16 artifact scope. Source lifecycle generation snapshot is a separate check: original artifact generation is not interchangeable with a later source-generation tombstone bump.

Both current/desired profile pointers reference `(legacy_id, profile.id, version.id)`; add these circular FKs after creating both tables. Assets/jobs reference the same scoped version tuple. A foreign version from a second companion or source artifact cannot satisfy a globally unique ID alone. All application joins include Legacy and parent/version IDs; database FK success does not imply access permission.

No global cross-Legacy deduplication by portrait hash, shared face cache, shared blob URL or face-comparison index. Assets are unique per attempt/version, even if images are byte-identical across two Legacies. SQL constraints protect storage metadata relationships; content reads still perform current owner/viewer authorization.

## 32. Performance/mobile

Proposed budgets, requiring measured validation rather than assumed desktop GPU capability:

| Resource | V1 bound/target |
| --- | --- |
| Portrait content | 512×512 normalized working crop; 256×256 poster; atlas maximum 1024×1024 |
| Encoded bundle | <=2 MiB total, <=3 assets; poster <=100 KiB; strict rig JSON <=128 KiB |
| Mesh | <=512 vertices, <=1,024 triangles; fixed attributes, no continuous ML, no per-frame pixel readback |
| Rendering | One portrait context; 30 fps target, 20 fps low-end; canvas backing edge <=768, DPR capped at 1.5 |
| Visual memory | Track allocations with a 32 MiB incremental visual budget; at most old/new bundles during swap. GPU-driver overhead is measured, not exactly knowable from compressed sizes. |
| Main-thread cost | <4 ms/frame p95 portrait work on target devices; degrade at sustained missed frames or context loss |
| Loading | Show initial immediately; load poster then one complete bundle; <=5 s load deadline, then fallback/retry on explicit later action or healthy manifest refresh |
| Bandwidth | No per-response assets; manifest refresh every 5 s while visible only; no offline private persistence |
| Worker | One prep at a time, one CPU-bound subprocess with 120 s deadline; start with 512 MiB working target/768 MiB hard cap, benchmark alongside existing workers before enablement |
| Storage | Normally <=6 MiB per Legacy for current/candidate/retiring bundles, excluding original and cleanup backlog; backlog quota stops new preparation, not purge |

Only WebP/PNG/JPEG raster decoding is needed; no video codec/container/autoplay dependency in the selected recipe. Use the vendored Three.js with a small dedicated flat mesh/material, not a cloned particle scene or new runtime CDN. The [WebGL lifecycle guidance](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API/WebGL_best_practices) supports explicit resource disposal and conservative buffer budgeting; these numerical limits are this proposal's targets, not browser guarantees.

No assets preloaded on homepage or while merely listing Legacies. Owner preview can show neutral/speaking synthetic envelope without synthesizing voice or recording a conversation. Test 390px layouts and real low-end Android/foreground iOS Safari; viewport emulation is not mobile performance acceptance. No 60 fps requirement, background animation or battery-heavy inference.

## 33. Reduced motion/accessibility

Honor `prefers-reduced-motion: reduce` and provide a user-controlled static-presentation option. Use the approved static poster with no blink, breathing, head/mouth motion or animated crossfade; retain spoken audio and textual call state. No forced motion necessary to understand the conversation.

Portrait canvas remains decorative (`aria-hidden`) beside existing subject title/disclosure/status; avoid exposing every frame/energy update to assistive technology. Keep native modal focus containment, visible focus, keyboard-operated setup/crop adjustments, readable error/status labels and existing large controls. Provide numeric/keyboard crop movement/zoom, not drag-only controls. User motion preference is local presentation state, not a Legacy fact.

## 34. Fallback behavior

Priority: valid animated approved bundle -> still-authorized approved static poster when motion/renderer fails -> existing Legacy initial. If authorization/consent/source/current version is invalid, skip all cached photo fallbacks and use the initial. Rya always uses her current visual/fallback behavior, never a human portrait.

Provider failure affects setup status only. Loading timeout, unsupported WebGL/image decode, allocation pressure, malformed rig or visual exception disposes that visual and leaves microphone/audio/Stop/End working. On reconnect, revoke playback identity and revalidate manifest before installing anything; do not replay animation. A static preview or fallback creates no new source asset or server row on read.

## 35. Database/migration plan

Propose additive `0021_visual_companions` after 0020. Create the four tables described above and the L16 source `processing_purpose` column with NOT NULL default `source_review`, CHECK of `source_review/visual_reference`. Image-only purpose restriction is checked in the service and, where possible, a row CHECK. Do not edit 0017–0020 or add canonical columns. Backfill only this constant purpose, not portrait assumptions or Legacy images.

Additional constraints: one profile per Legacy; positive revision/version; unique `(companion_id, version_number)` and `(companion_id, request_key)`; state CHECKs; lease token/expiry both null or both nonnull; bounded nonnegative attempts/sizes; one available asset per `(version_id, logical_role)` via partial unique index; globally unique storage `(backend,key)`. Use nullable approval fields as a complete pair. Active-pointer eligibility is a transaction/service invariant, not something a FK alone can enforce. Enabled requires nondeleted profile and nonnull current pointer; setting pointers/disable must satisfy checks atomically.

Indexes: source -> dependent versions; Legacy/profile/version scope; job kind/state/next-attempt/lease-expiry claim index; asset state/cleanup-age index; version request key; quota counting by confirmer/admission time. Avoid JSON-only authorization or a second duplicate asset manifest that can drift from registered rows.

Deletion uses RESTRICT/scoped relationships and explicit tombstones/purge, not cascade-delete of canonical or L16 originals. User confirmer/approver may use SET NULL on account erasure while retaining non-content dates/receipt. Do not implement account/Legacy hard deletion incidentally; integrate any future such command with registered purge first.

Transaction order for L19 writes: owner quota lock if needed -> Legacy -> relevant source rows sorted by ID -> companion -> versions sorted by ID -> job -> assets. Existing source delete already takes Legacy then source; its L19 invalidation hook follows this order. Discover candidate IDs without locks, then lock parents and revalidate before changing jobs; do not claim job-first then request a source lock. For replacement spanning sources A/B, read IDs, lock both in order, lock profile and retry if its pointers changed. No lock across storage/CPU/provider work. Read authorization uses fresh sessions, not stale ORM identity maps.

Migration gates later: PostgreSQL upgrade/downgrade/re-upgrade on empty disposable DB; validate FKs/indexes/checks; preserve historical data and legacy purpose defaults; JSON fields portable to current test infrastructure. Downgrade is destructive once populated and is not production rollback. Disable feature/worker preparation first and preserve schema for recovery/purge.

## 36. API plan

All proposed paths are under `/api/v1/legacies/{legacy_id}/visual-companion`. No public provider callback or API accepts storage keys. Feature flags fail closed; GET does not create rows/jobs.

| Method/path | Contract | Authorization |
| --- | --- | --- |
| `GET /capabilities` | Feature availability, owner-managed indication, limits; no private source/version detail | Existing builder or active viewer scope, purpose-specific fields |
| `GET /` | Current/desired status, safe version/job summary, available owner actions | Current owner only |
| `POST /versions` | Source ID, crop/rotation, confirmation-copy version + affirmative confirmation, request key, expected revision; select/replace/regenerate through one command; 202 | Current owner only; valid same-Legacy original; quota and digest checks |
| `GET /versions/{version_id}` | Bounded job/validation state; no raw provider output | Current owner only |
| `GET /versions/{version_id}/manifest` | Exact ready private preview bundle digest and relative content paths, short lease | Current owner only |
| `GET /versions/{version_id}/assets/{asset_id}/content` | Preview asset bytes; ready version; no deleted/purge-pending content | Current owner only |
| `POST /activate` | Version ID, expected profile revision, exact previewed bundle digest, affirmative approval; atomic pointer/enable change | Current owner only; latest desired ready version and source still eligible |
| `PATCH /` | `{enabled, expected_revision}` only; no arbitrary metadata update | Current owner only; re-enable requires existing valid approval |
| `DELETE /` | Expected revision; logical delete + durable purge, 202; idempotent tombstone | Current owner only |
| `GET /active-manifest` | Enabled approved current version and minimal safe relative paths; valid-until/duration and revision; empty/unavailable safely | Current active viewer only; owner preview remains separate unless owner also has viewer grant |
| `GET /active/assets/{asset_id}/content` | Only assets of the currently approved enabled version | Current active viewer only; fresh source/profile/membership checks |

Generation status is the version GET, not a separately enumerable job API. Retry is a new explicit version request after failure, with quota and fresh confirmation; transport duplicate request reuses its receipt. No independent settings route, freeform prompt endpoint or arbitrary regeneration vendor selector. DELETE/toggle replay must never re-enable; an already applied activation replay may report its receipt/current state but cannot roll back a newer pointer.

HTTP semantics: 401 unauthenticated; 404 inaccessible/foreign; 403 forbidden action for an already visible capability; 409 revision/digest/lifecycle conflict; 410 known removed owner resource if appropriate; 413 bounds; 415 format; 422 crop/confirmation/rig validation; 429 quota; 503 unavailable storage/preparation. All errors sanitized/no-store. A version's source IDs/hashes/confirmation are owner-only; active visitor manifest contains no original-photo route or provider/storage metadata.

## 37. Frontend plan

Builder: add an owner-only Visual Presence destination alongside existing Memories/Media/Timeline/Stories. Use selected Legacy snapshot/epoch and existing drawer/modal conventions. Flow: choose existing image or L16 visual-reference upload -> orient/crop -> quality guidance -> explicit confirmation -> queued/preparing status -> motion preview -> approve/activate. Show current separately from candidate, with replace/regenerate/disable/delete controls and clear “original photo remains” delete wording. No Rya tool or conversation prompt creates setup state.

Proposed modules: `visual-presence-client.js` for strict authorized API calls; `visual-presence-settings.js` for owner setup; `visual-presence-controller.mjs` for identities/lifecycle; `legacy-portrait-renderer.mjs` for bounded Three.js rendering; a small CSS file. Reuse media selection/upload components with the new purpose flag and existing blob cleanup. Do not mix source-review Preserve actions into visual setup.

Live integration: change only the Legacy presence slot in `live-voice.mjs`; retain Rya branch. Add local fenced playback observation/reset hooks in `realtime-playback.mjs` and `realtime-client.mjs`; no WSS schema change. Preserve drafts/New Chat/first accepted utterance and existing chat adapter refresh. Setup/preview and call contexts never share one mutable renderer instance.

Cleanup on call end, navigation, logout, Legacy/chat switch, pagehide, context loss and background: increment identities first; abort fetches; unsubscribe envelope/state; close/reset mouth; cancel RAF/blink/transition timers; disconnect resize/media-query listeners; release ImageBitmaps, texture/material/geometry/context references; revoke all blob URLs; remove canvas. Keep teardown idempotent. Disposal does not stop audio itself; the existing L15 lifecycle owns that. Barge-in clears only speaking work, not the whole approved portrait. Version replacement disposes only the retired bundle after atomic swap. Background retains existing L15 call-ending behavior, not an L19 background mode.

## 38. Test plan

Future coverage; no tests below were executed in Phase A. Use established pytest fake DB/storage/providers, API dependency overrides, Node fake clocks/audio contexts and later real browser/device checks. Keep malicious portrait/geometry fixtures synthetic or explicitly consented; no customer images in Git.

| Area | Mandatory scenarios/assertions |
| --- | --- |
| Owner/source setup | Same-Legacy image accepted; foreign source denied; owner confirmation/crop/digest durable; no identity inference; idempotent identical request; conflicting digest 409; safe old/photo warnings; invalid/group crop rejected or recrop requested. |
| Presentation-only intake | Visual-reference reservation/receive/validation/retry creates zero evidence/candidates/canonical/personality/progression; ordinary L16 uploads still extract normally; collaborator cannot select special purpose; purpose immutable and included in digest. |
| Jobs/versioning | Duplicate admission/claim; bounded attempts/quota; lease expiry; invalid/stale result; delete while preparing; no worker activation; ready preview stays private; stale preview approval rejected; new desired version fences old completion; old replay does not restore state. |
| Authorization | Owner-only every mutation and preview; collaborator zero mutations/private previews; pure visitor active-only; owner without viewer grant not silently allowed persona access; dual roles evaluated separately; revocation while loading/generating. |
| Isolation | Source, original artifact, candidate version, current pointer, preview, job and asset from another Legacy/profile rejected; direct composite FK violations fail PostgreSQL. |
| Zero-effects snapshots | Compare Memory, MemoryRevision, LifeEvent/support, Story/version/provenance, SourceEvidence/candidates, Personality/jobs, relationship/profile, builder activity/progression, Conversation/Message/turn/effect receipts before/after each visual operation. Existing unchanged unrelated L16 jobs are isolated, not counted as avatar work. |
| Deletion | Source delete before/after generation/activation; current A plus candidate B cases; avatar delete retains original and unrelated evidence; disable retains assets but denies viewers; repeated delete; storage outage; late PUT; restart sweep; no resurrection. |
| Asset security/storage | Full decoder bounds, EXIF strip, malformed rig/NaN/indices/seams, complete bundle required; SSE-C live disposable checks; anonymous/wrong-key denied; exact-key/version cleanup; no private bytes/URLs/credentials in logs/API/static build. |
| Owner preview/visitor privacy | Unapproved asset inaccessible; source original unavailable to visitor; no source filename/crop/provider internals in active manifest; no cached portrait after authorization expiry; no export feature. |
| Animation | Loading/idle/listening/thinking/speaking/error/disabled; silence closes mouth; energy from accepted output only; microphone/ambience never drives mouth; output-clock scheduling and long-frame internal silence; reduced motion static. |
| Interrupt/fencing | Local barge-in, Stop, provider-only cancel, provider-done-before-drained; immediate neutral frame; stale audio/RAF/decode never restarts; next generation works; disconnect/reconnect and wrong response IDs fail closed. |
| Lifecycle/performance | Silent New Chat no Conversation; first final binds once; end during preload/speech; Legacy/chat/version/user switch; background; failure fallback; 50 repeated calls/100 swaps with no retained blobs/listeners/timers/textures and bounded memory. |
| L15/L12 regression | No receipt/persistence changes, interrupted assistant tail not persisted, WSS/tickets/provider secrets intact, exclusivity/dictation/TTS unaffected, Rya particle/ambience and ree-yah pronunciation contract unchanged. |
| Full integration | Existing L13–L18 backend and frontend suites; current-information/relationship/owner-memory-DELETE and retrieval behavior unaffected. Real mobile and subjective likeness/blink/mouth checks reported separately, never inferred from a mock or viewport. |

Browser motion tests should use rendered-frame assertions at scheduled audio times, not only string comparisons of state labels. Test that a visual observer throwing cannot fail microphone/audio playback. Assert no generation/model request occurs on call start/response/playback. Test ordinary source deletion factual-support semantics separately from visual-reference zero-effects deletion.

## 39. PostgreSQL concurrency plan

Use retained trusted PostgreSQL 17.11 only with a fresh disposable loopback DB, e.g. `l19_test_phase_b`, isolated SCRAM credentials and exact database/host guard. Never point opt-in fixtures at production. Use independent connections, barriers/events, real commits and rollback assertions; sleeps alone do not establish a race. Existing L16 tests include historical migration-head assertions, so do not claim every old opt-in suite automatically targets the latest migration unchanged. Maintain explicit isolated migration targets or intentional head-contract updates later.

| Race | Required coherent result |
| --- | --- |
| Activate v1 vs stale v0 completion | v0 cannot publish as desired/current or change enabled/approval. v1 pointer remains owner-selected. |
| Regenerate vs companion delete | Either new candidate is admitted before deletion and cancelled, or request conflicts. Tombstone cannot be cleared by completion/replay. |
| Source delete vs result publish/owner activate | Same Legacy/source locks determine winner; deletion revokes dependent availability and queues all derivatives; no deleted-source current manifest. |
| Simultaneous activation | Expected revision/desired ID permits one winner; loser conflicts, not last-write-wins with stale owner intent. |
| Duplicate request key | One version, one job/quota charge and one logical bundle; mismatched payload key conflicts. |
| Lease expiry/two worker completion | Only current lease may publish; stale attempt assets tracked/purged; no duplicate available logical roles. |
| Cross-Legacy composite constraints | Profile pointers, version-source/original artifact, job/version and asset/version mismatches fail at DB level. |
| Transaction rollback | Inject failure after asset registration, confirmation, quota admission, activation and delete/purge enqueue; no partial pointers/approval/forgotten cleanup; external writes remain discoverable. |
| Cleanup vs late PUT/worker crash | Original purge cannot mark final erased while a registered writer may still complete; post-deadline sweep removes late exact-key/version objects. |
| Delete source A / prepare B / activate B | No deadlock; A revoked; B remains private until owner action; stale A cannot invalidate unrelated valid B after its activation. |
| Read/asset-load vs disable/revocation | New server reads denied after commit; compliant preloaded client reaches fallback by lease deadline; do not assert retracting already copied bytes. |
| Old idempotent replay vs newer activation | Historical receipt returned without pointer/enabled/desired mutation, as required by L18 replay integrity. |

Exercise bounded lock timeouts and deterministic lock-order tests. Repeat both winning orders. SQLite passing is not concurrency acceptance. After any race fix rerun failing case, full L19 PostgreSQL matrix, focused domain/browser tests and full backend/frontend regressions. Validate cleanup, DB drop and cluster stop while retaining the trusted runtime.

## 40. Production/deployment implications

Future release only: flags `VISUAL_PRESENCE_ENABLED` and `VISUAL_PREPARATION_ENABLED`, default false; active reads/animation flag can be disabled without disturbing voice. Purge remains available independently. Add a dedicated systemd visual worker with restart policy, restrictive user/umask, private temp, resource caps and no-new-privileges, patterned on but not merged into the media worker. Restrict preparation child network access; parent may access authorized DB/storage. Benchmark total host headroom before enabling even one job; this audit does not establish spare CPU/RAM or installed model weights.

No cloud visual-provider credential is needed for selected local preparation. Later provisioning downloads only vetted pinned packages/model assets, verifies checksum/license, and retains a reproducible manifest. No model download from request handlers. Store private derivatives in existing configured object storage under the new namespace; monitor cleanup backlog, bucket versions/capacity and SSE-C recoverability. No bucket or key changes in Phase A.

Deploy schema and backward-compatible disabled backend/worker before frontend; test ordinary media defaults and no-evidence-purpose branch before enabling intake. Use the existing Git/Vercel static deployment flow for code only. No private portrait in static assets, build output or CDN cache. Existing same-origin API rewrite/direct authorized media transport is reused. Confirm actual proxy byte/time limits for selected L19 routes without broadening all JSON/WSS limits. WSS location, brain configuration and audio model/voices need no change.

CSP: the sampled public responses currently have no CSP, but release must recheck real headers. This recipe needs only same-origin scripts, trusted API connect origin and authorized Blob images if used; no third-party model/image/video origins, `unsafe-eval`, or microphone/camera permission change. If enforcing CSP later, inventory current inline bootstrap/imports separately rather than accidentally breaking auth/homepage. Do not claim a restrictive CSP already protects this feature. Static modules need consistent versioned import URLs/build IDs to avoid cached old-client/new-manifest mismatches; reject unknown recipe versions to fallback. Never apply public static-cache rules to authenticated visual routes.

Release gates: fresh backup; isolated migration/PG acceptance; full regressions; consented synthetic QA only; real storage privacy/erasure; renderer quality/performance; backend/media/personality/visual service health; normal Rya, visitor, L12/L15, L16–L18 behavior. Routine rollback disables visual feature/preparation, leaves audio/schema intact and continues purge. No tag/deployment is authorized by this architecture document.

## 41. Risks/open questions

| Risk/gate | Decision now / verification still required |
| --- | --- |
| Natural blinking/mouth quality on old photos | Chosen 2D rig is restrained but unproven on this product. Later consented image-quality spike must pass neutral/blink/open-mouth extremes on a representative set. Do not call a landmark model a ready talking-avatar product. |
| Mouth interior not present in photograph | No invented teeth/tongue or large jaw opening. Reject/degrade unsuitable animated candidates; do not promise a wide-mouth realistic speaker. |
| Package/model licensing and native compatibility | Pin and review actual artifact licenses/checksums and isolated worker compatibility; code/documentation license alone is insufficient. No dependency/model was installed or downloaded here. |
| Zero factual effects vs normal L16 upload | Explicit presentation-only purpose/validation path is necessary. Requires focused L16 backward-compatibility tests; cannot use normal extraction and still claim zero evidence. |
| Voice policy | Keep actual per-user L15 preference; no Legacy voice selector. Any future owner-chosen shared voice is a separate product change, not hidden in visual setup. |
| Consent/rights for deceased subjects/minors | Durable owner declaration + version approval proposed; appropriate policy/legal review remains a release gate. No legal sufficiency claim. |
| Cached-byte revocation | Server denial immediate; compliant viewer lease <=15 seconds; delivered bytes cannot be remotely un-copied. Confirm this product privacy tradeoff before release. |
| Storage ambiguous writes/erasure | Add exact-key/version reconciliation and bounded writes; verify late-writer cleanup against disposable real S3/SSE-C. No untracked object leaks. |
| Server capacity/mobile quality | Budgets are targets. Benchmark actual worker coexistence and real foreground Android/iOS devices; fallback does not excuse a failed quality gate. |
| CSP/cache/auth transport | Public HEAD sample verified, not every route. Preserve strict bearer host/path allowlists and no-store private responses; no blanket CSP relaxation. |

These gates do not make the technique/model/API undecided. Phase B can implement the specified contracts with fake preparation/storage; real adapter enablement and subjective visual acceptance wait for their explicit gates. If the bounded 2D approach cannot meet the quality bar, return with evidence for a product decision rather than silently shipping a hybrid/neural video system.

## 42. Recommended Phase B implementation sequence

1. Freeze additive DTOs, permissions, zero-effects snapshots, recipe/asset limits and the local-only provider interface. Start with fake bundles; no production assets.
2. Add proposed 0021 schema/constraints and isolated migration tests; include immutable L16 purpose defaults and owner-only validation-only intake. Confirm normal L16 intelligence/review is unchanged.
3. Implement source/crop confirmation, version/idempotency/quota commands and owner-only preview/activation; test old-key replay and source availability before any external work.
4. Implement dedicated worker claim/leases and bounded fake preparation, reserve-before-write asset registry, purge/late-write reconciliation and source-deletion hooks. Prove no canonical/evidence/personality side effects.
5. Execute PostgreSQL barriers for both orders of every release-critical race. Fix only evidenced defects; rerun the full matrix and regressions.
6. Implement private owner/active-viewer manifest and exact authorized asset delivery, expiry/revocation and no-store/auth fetch contracts. Test source secrecy and dual-role boundaries.
7. In the separately authorized implementation stage, vet/pin the local geometry adapter and perform a consented offline quality/resource spike. No live-response generation and no automatic external vendor fallback.
8. Phase C later implements setup/portrait renderer/playback observer and exact cleanup/fencing. Use actual AudioContext/render tests plus real device/subjective preview acceptance; retain L15 as authority.
9. Release only after all agreed gates, storage/privacy/consent review and explicit production authorization. Stop this Phase A at the architecture document; do not proceed into Phase B.

Phase A verification: all 42 numbered sections appear in order; local report links, UTF-8 encoding and new-file/tracked whitespace checks passed. SHA-256 comparisons confirmed all five pre-existing dirty/untracked files listed in section 2 remained byte-for-byte unchanged. The only new task file is this architecture document; no commit was created. Application tests, migrations, provider probes and runtime benchmarks were not run because no implementation occurred. All future acceptance counts remain unclaimed.
