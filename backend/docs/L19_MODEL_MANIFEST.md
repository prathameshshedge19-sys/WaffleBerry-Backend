# L19 local geometry model and package manifest

Research date: 2026-09-08. Current status: **Real Linux provider, offline confinement, resource and integrated lifecycle gates passed; Phase B accepted**. Section 10 is the final provider/provisioning evidence and supersedes historical execution blockers in sections 7–9. Final regression and cleanup evidence is recorded in the Phase B report. Production enablement/deployment is not authorized.

Scope: the Phase A `portrait_2d_v1` offline preparation candidate, MediaPipe Face Landmarker, for CPython 3.12 on Windows/Linux. Read alongside [Phase A sections 6-10 and 12](L19_PHASE_A_ARCHITECTURE.md). The Phase B attachment dated by this research session requires separate package/weight evidence and forbids enabling the real adapter when actual model use/redistribution permissions are unclear.

## 1. Decision and correction of the earlier research finding

- **Model weight license evidence: established as Apache-2.0 through Google's official model-card/download chain.** This is model-specific evidence, not an inference from the MediaPipe package's code license.
- **No explicit conflicting license, noncommercial-only condition, or redistribution prohibition was found for the inspected model bundle or its geometry metadata.** The bundle has no standalone LICENSE/NOTICE entry; that absence is not a conflicting license.
- Google's geometry metadata source has its own Apache-2.0 header, and its build target expressly generates `geometry_pipeline_metadata_landmarks.binarypb`. The source-to-binary correspondence is supported by that official build mapping; this research did not reproduce the binary byte-for-byte. That is a reproducibility limitation, not evidence of a different license or, by itself, a local-use blocker.
- **Local/server-side commercial use and redistribution are distinct.** L19 can provision the unmodified model privately from upstream, verify its digest, and use it in a private worker without committing weights to Git or delivering them to clients. That proposed workflow is supported by the documented permissions. Apache-2.0 also permits redistribution subject to its conditions; a future distribution of model/package bytes must retain the applicable license/notices and assess the actual distribution contents. Merely excluding a file from Git does not settle whether a later installer/container constitutes distribution.
- The earlier statement that complete bundle redistribution clearance was blocked solely by an unreproduced metadata binary was too strong. **No substantive conflicting model license has been identified.** This manifest records the evidence and its limits, not a legal opinion or blanket audit of all native/transitive components.
- `LocalPortraitRigProvider` is implemented and verified on Linux CPython 3.14.4, including the final native failure and domain lifecycle matrix (section 10). CPython 3.12 and other platforms are not thereby established. All production feature flags remain disabled; this document changes no production runtime configuration.

The governing [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0), sections 2-4, grants reproduction/use/distribution rights without a noncommercial restriction and specifies redistribution conditions. Preserve the license, applicable attribution/NOTICE information, and notices of modifications when applicable. No weight-license conclusion here is based on the Google documentation footer, which licenses page content and code examples separately.

## 2. Exact model artifact and provenance

| Field | Value |
| --- | --- |
| Upstream publisher | Google / MediaPipe |
| Task | Face Landmarker |
| Candidate variant/version | `face_landmarker/face_landmarker/float16/1` |
| Filename | `face_landmarker.task` |
| Numbered download | <https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task> |
| Size | 3,758,596 bytes |
| SHA-256, measured from downloaded bytes | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |
| GCS object generation | `1683136941916318` |
| Last-Modified | `2023-05-03 18:02:21 UTC` |
| GCS supplied MD5 / ETag | `b0e7274907a1644404fef66b28dd6d85` |
| GCS supplied CRC32C, base64 | `2FSEdQ==` |

Primary provenance chain:

1. Google's [Face Landmarker model table](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker#models) describes the three model components and links both the downloadable bundle and each model card.
2. The table's [latest bundle](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task) was fetched into memory on the research date and had the same size and SHA-256 as numbered version 1. The latest object's generation was `1683136941468629`; it is a different object with identical observed bytes.
3. Google's [official sample](https://github.com/google-ai-edge/mediapipe-samples-web/blob/main/src/tasks/face-landmarker.ts) explicitly uses the numbered `/float16/1/face_landmarker.task` URL.
4. The ZIP-compatible task archive was inspected in memory without model execution. Its complete four-entry inventory is below.

The SHA-256 above is a researcher-computed digest of the official HTTPS download, not a Google-published signed SHA-256 attestation. The numbered path plus expected SHA-256 is the candidate pin. Do not rely on `latest` or assume numbered URLs are cryptographically immutable. No model file was saved to the repository or installed.

| Archive member | Bytes | SHA-256 | License evidence |
| --- | --- | --- | --- |
| `face_detector.tflite` | 229,746 | `b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f` | Apache-2.0; BlazeFace Short Range model card |
| `face_landmarks_detector.tflite` | 2,553,590 | `c7d54204ce0448474c7f3fa9af494787c0965cbdd6f20fc72867e43046bd43d5` | Apache-2.0; FaceMesh V2 model card |
| `face_blendshapes.tflite` | 955,312 | `4f36dded049db18d76048567439b2a7f58f1daabc00d78bfe8f3ad396a2d2082` | Apache-2.0; Blendshape V2 model card |
| `geometry_pipeline_metadata_landmarks.binarypb` | 19,376 | `bdbcda96dfcb7da883da124aaa2c55dee49770d934f0fcc71747f8c21bdc75b4` | Apache-2.0 source header and explicit binary-generation target; exact binary reproduction not performed |

## 3. Model license evidence, independently of package code

Each Google model card explicitly identifies Apache License, Version 2.0 under its model licensing field on page 1:

| Component | Model card date | Exact primary source |
| --- | --- | --- |
| BlazeFace Short Range | 2021-06-09 | [Google model card](https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20%28Short%20Range%29.pdf) |
| FaceMesh V2 | 2022-09-15 | [Google model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf) |
| Blendshape V2 | 2022-11-11 | [Google model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf) |

Association of these cards with the exact downloaded weights is an evidence-chain conclusion from Google's model table and the byte-identical latest/version-1 downloads. The cards do not individually publish the component SHA-256 values. No alternate model, third-party mirror, conversion, or similarly named TensorFlow.js model was substituted.

Geometry metadata has additional direct primary evidence:

- [Apache-licensed source at revision `22fce9e136d7f733ade40db0cfee89a92c687c0e`](https://github.com/google-ai-edge/mediapipe/blob/22fce9e136d7f733ade40db0cfee89a92c687c0e/mediapipe/tasks/cc/vision/face_geometry/data/geometry_pipeline_metadata_landmarks.pbtxt).
- [Build target at that revision](https://github.com/google-ai-edge/mediapipe/blob/22fce9e136d7f733ade40db0cfee89a92c687c0e/mediapipe/tasks/cc/vision/face_geometry/data/BUILD), using `encode_binary_proto` to map that input to the exact `.binarypb` basename in the archive.
- [Official source history endpoint](https://api.github.com/repos/google-ai-edge/mediapipe/commits?path=mediapipe/tasks/cc/vision/face_geometry/data/geometry_pipeline_metadata_landmarks.pbtxt&per_page=1), which identified that revision, dated 2023-03-01.

Do not construe an absent standalone archive license, or failure to independently reproduce a compiler output, as an explicit conflicting term. Conversely, do not label an independently regenerated binary or new model revision as verified under the old digest.

L19 still requires owner confirmation, geometry only, multiple-face ambiguity handling, private assets, and no recognition or demographic/emotional inference. Optional blendshape output remains disabled by architecture; its weights are still present in this unchanged upstream bundle and therefore included in the license inventory.

## 4. MediaPipe 1.0.1 package candidates and inspected licenses

Primary release sources: [PyPI release](https://pypi.org/project/mediapipe/1.0.1/), [versioned metadata JSON](https://pypi.org/pypi/mediapipe/1.0.1/json). Release date: 2026-08-14; current release when researched.

| Candidate environment | Exact wheel | Bytes | SHA-256, PyPI and independently verified download |
| --- | --- | --- | --- |
| Windows x86-64, CPython 3.12 | `mediapipe-1.0.1-py3-none-win_amd64.whl` | 20,103,114 | `96dc9de6bd04a6315ef424fda5c48e0929f2d78317295e75bc32c0bceeab517b` |
| Linux x86-64, glibc >=2.28, CPython 3.12 | `mediapipe-1.0.1-py3-none-manylinux_2_28_x86_64.whl` | 37,881,482 | `121522251afc3c135e4b7b0c341dd5e050ad1ec87631127484f3c389ae385044` |

Exact wheel download URLs:

- Windows: <https://files.pythonhosted.org/packages/22/71/42365b0aec2a96dfbeb3441220fe8dccd9a833f36adfecf3aa9f211c449b/mediapipe-1.0.1-py3-none-win_amd64.whl>
- Linux: <https://files.pythonhosted.org/packages/2a/58/bdd5bada89d7a132375df05e962bf702c148b47043dca98d820d9395152b/mediapipe-1.0.1-py3-none-manylinux_2_28_x86_64.whl>

Both wheels were initially downloaded only into the workspace's `backups/l19tmp/`, outside the backend repository, and opened as ZIP files without installation or execution. Subsequently, the user authorized installation of the Windows candidate into a separate backup venv (section 9). Neither MediaPipe wheel has been imported or natively executed by this task. Their unabridged embedded LICENSE/NOTICE remain available in those wheel files.

The following entries are byte-identical between the two wheels:

| Archive entry below `mediapipe-1.0.1.dist-info/` | Bytes | SHA-256 |
| --- | --- | --- |
| `METADATA` | 10,688 | `5ea0016039f80d34f1d4d262efe79535ae0c2519168181518faaffca6942a7e2` |
| `licenses/LICENSE` | 12,331 | `8707eef0533987efc5b155d64761eeb6e20793f50b9bd1a68dad1cf4719d0ed8` |
| `licenses/NOTICE` | 1,569,742 | `e8e3eddc5c36d7413635455933650d7423b937185180e393f9a006bee60162e7` |

License inventory and limits:

- `METADATA` declares `License: Apache 2.0` and names both LICENSE and NOTICE.
- The actual LICENSE contains the Apache-2.0 text plus a Lucent Technologies / Rob Pike / Ken Thompson permissive notice for `tasks/cc/text/language_detector/custom_ops/utils/utf/`. That additional notice permits use/copy/modification/distribution with notice retention. Do not describe the entire file as Apache text only.
- The complete NOTICE contains the MediaPipe Tasks Privacy Notice and extensive third-party notices. Inspection found Apache, BSD, MIT, MPL, EPL, and GPL texts, including tool/runtime exceptions and alternative-license contexts. Their presence must not be relabeled as an all-Apache package, a blanket GPL license on Face Landmarker weights, or proof that every mentioned source/build tool ships in a given runtime. This research did not perform a complete native linkage/component applicability audit. Preserve the full exact NOTICE; the hash and retained wheels identify all its contents without an incomplete replacement notice list.
- Package license evidence does not confer a license on independently downloaded model weights; section 3 provides that evidence separately.

## 5. Python/platform compatibility: evidence versus testing

Google's [Python setup guide](https://developers.google.com/edge/mediapipe/solutions/setup_python) lists Windows/Linux and Python 3.9 or later. Both inspected wheels classify Python 3.12. Their METADATA has **no `Requires-Python` field**, so do not invent an explicit package-level bound. Both `WHEEL` entries report `Root-Is-Purelib: false`; the `py3-none` tag does not mean these packages contain no native libraries.

Windows x86-64 and Linux glibc >=2.28 x86-64 are candidates, not confirmed deployment environments. Current PyPI also publishes Windows ARM64 and Linux aarch64 wheels; those were not downloaded or tested. No musl/Alpine, 32-bit Windows, GPU, or production-host compatibility is established by this research.

The initial research ran no installer or native tests. The subsequent authorized isolated Windows installation and `pip check` passed (section 9). **No MediaPipe import, native library load, FaceLandmarker creation, inference, offline runtime test, or performance test was run.** The application has existing changes including `Pillow==12.3.0` in `backend/requirements.txt`; this task leaves them unchanged. Linux runtime availability is false in the main task's environment evidence: its elevated `wsl --list --quiet` returned exit 0 with empty output (no listed distributions). The main task also reports Docker absent. This research did not rerun those checks; no Linux test is claimed.

## 6. Dependency metadata and Pillow 12.3.0

Exact `Requires-Dist` declarations are identical in both MediaPipe wheels:

```text
absl-py~=2.3
certifi
numpy
sounddevice~=0.5
flatbuffers~=25.9
opencv-contrib-python
matplotlib
```

There is **no direct Pillow, protobuf, JAX, TensorFlow, or NumPy upper-bound requirement in MediaPipe 1.0.1's metadata**. Do not carry constraints from older MediaPipe releases into this candidate. This statement describes declared direct dependencies, not native binary ABI compatibility.

The following were initially observed current dependency versions fitting the direct version ranges. The later Windows resolver selected these versions; section 9 records the complete installed package set and installation-report hash. Native compatibility remains untested. Each link identifies exact versioned primary metadata:

| Dependency candidate | Python bound | Declared license / metadata evidence | Relevant constraints |
| --- | --- | --- | --- |
| [absl-py 2.5.0](https://pypi.org/pypi/absl-py/2.5.0/json) | >=3.10 | Apache-2.0 | Fits `~=2.3` (>=2.3,<3.0) |
| [certifi 2026.7.22](https://pypi.org/pypi/certifi/2026.7.22/json) | >=3.7 | MPL-2.0 | No declared dependencies |
| [numpy 2.5.3](https://pypi.org/pypi/numpy/2.5.3/json) | >=3.12 | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | Satisfies the observed OpenCV/Matplotlib NumPy requirements |
| [sounddevice 0.5.6](https://pypi.org/pypi/sounddevice/0.5.6/json) | >=3.7 | MIT | Requires `cffi`; optional extra `numpy`; fits `~=0.5` |
| [flatbuffers 25.12.19](https://pypi.org/pypi/flatbuffers/25.12.19/json) | Unspecified | Apache 2.0 | Fits `~=25.9` (>=25.9,<26.0) |
| [opencv-contrib-python 5.0.0.93](https://pypi.org/pypi/opencv-contrib-python/5.0.0.93/json) | >=3.6 | Apache 2.0 package metadata; bundled third-party terms require separate inventory | Requires `numpy>=2` for Python >=3.9; no Pillow declaration |
| [matplotlib 3.11.1](https://pypi.org/pypi/matplotlib/3.11.1/json) | >=3.11 | Matplotlib license agreement and bundled third-party licenses | Requires `pillow>=9`, `numpy>=1.25` |
| [pillow 12.3.0](https://pypi.org/pypi/pillow/12.3.0/json), existing backend pin | >=3.10 | MIT-CMU package license expression | No mandatory base `Requires-Dist`; listed dependencies are extras |

Matplotlib 3.11.1 additionally declares `contourpy>=1.0.1`, `cycler>=0.10`, `fonttools>=4.28.2`, `kiwisolver>=1.3.1`, `packaging>=20.0`, `pyparsing>=3`, and `python-dateutil>=2.7`. These plus `cffi` and their dependencies were resolved for the isolated Windows installation in section 9; there is no Linux lock or native acceptance result.

Matplotlib's metadata also inventories AMS/STIX/Last Resort fonts (OFL-1.1), BaKoMa Fonts Licence, ColorBrewer and SheenBidi (Apache-2.0), Courier 10 (Bitstream-Charter), FreeType (FTL OR GPL-2.0-or-later), HarfBuzz (MIT-Modern-Variant), JSXTools resize observer (CC0-1.0), libraqm/Qt4 Editor/Solarized (MIT), QHull (Qhull), and Yorick colormaps (a BSD-style notice). These are metadata declarations; exact platform-wheel contents and license applicability were not exhaustively audited. Likewise, NumPy/OpenCV/Pillow native third-party notices must not be reduced to the projects' top-level licenses. This is not a complete transitive/native SBOM.

**Pillow conclusion:** no declared version conflict was found between the examined MediaPipe 1.0.1 dependency path and `Pillow==12.3.0`. The relevant path is `mediapipe -> matplotlib 3.11.1 -> pillow>=9`; 12.3.0 satisfies that bound. CPython 3.12 satisfies the inspected Python bounds. The subsequent isolated Windows resolver/install and `pip check` passed with this Pillow pin. That establishes dependency-metadata consistency for that venv, not import/API/native compatibility or compatibility with the backend environment. An unconstrained future install may choose different dependency releases.

PyPI publishes CPython 3.12 Windows x86-64 and Linux x86-64 wheels for Pillow 12.3.0. SHA-256 values below were initially metadata-only; the Windows wheel was subsequently downloaded/installed by pip, with its hash recorded in the installation report. The Linux Pillow wheel was not downloaded:

- `pillow-12.3.0-cp312-cp312-win_amd64.whl`: `a2b55dd6b2a4c4b7d87ffa56bdb33fdc5fdb9a462173861a7bc097f17d91cb09`.
- `pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl`: `78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91`.

## 7. Actual unresolved acceptance questions

| Question | Evidence/status | Required next acceptance |
| --- | --- | --- |
| Exact model permissions | Official Apache-2.0 cards and source/build chain; no conflicting term identified | Retain evidence and recheck any artifact/version change; no invented local-use blocker from unreproduced metadata |
| Geometry binary reproducibility | Source/build mapping established; byte-for-byte rebuild not performed | Optional provenance strengthening; do not describe it as an explicit license conflict |
| Installation and full dependency closure | Authorized Windows backup venv installation and `pip check` passed; exact versions, URLs and artifact hashes retained in pip report | Native compatibility remains a separate gate; no backend dependency changes or Linux lock |
| Actual Python 3.12 Windows/Linux support | Windows package install only; native import/create/detect all NOT RUN. Main task's elevated WSL check listed no Linux distributions; Docker also reported absent | First establish verified offline/private execution, then scoped smoke tests on intended platforms with exact model hash and Pillow pin |
| Offline/private worker behavior | Verified offline/private execution unavailable: neither a supported metrics opt-out for the exact 1.0.1 wheel nor validated OS-enforced child egress denial was established | **Keep native execution stopped, including imports.** Review and validate an execution boundary that satisfies privacy and network restrictions; no mandatory opt-out-plus-OS-denial combination is attributed to the user |
| Native/transitive license inventory if distributing runtime artifacts | Full MediaPipe notices retained; whole closure/linkage applicability not audited | Assess actual packaged components, preserve applicable notices and fulfill their terms; do not imply weights are noncommercial because NOTICE mentions GPL |
| Quality and resources | No inference or spike run | Consented/synthetic offline acceptance for riggability, ambiguity, bounds, wall time, peak memory, and bundle validation |

The metrics disclosure is present in the **actual downloaded wheel**, not only a newer website: both its METADATA and NOTICE include the MediaPipe Tasks Privacy Notice dated 2026-06-05. It states image/video/text processing stays on device while usage/performance metrics are sent to Google. The [versioned PyPI description](https://pypi.org/project/mediapipe/1.0.1/) exposes the same disclosure. No runtime transmission or opt-out behavior was measured. Do not claim that installing a local model makes this package fully offline by default, and do not silently implement a workaround or substitute another model.

The separate CPython 3.12 Windows backup venv was subsequently created and populated after explicit user authorization (section 9). Native execution remains stopped pending verified offline/private execution. The user requires upfront privacy review and no credential use or unapproved network activity during native execution. Specific opt-out/confinement mechanisms are implementation-review matters, not additional user-authored requirements. The API process and ordinary tests must not acquire a mandatory MediaPipe dependency. Normal preparation must not download models. No real-adapter implementation or model load has occurred.

## 8. Work performed and boundaries

- Read the relevant local architecture and attachment licensing/hard-stop requirements; inspected the existing backend requirements and dirty-worktree status.
- Fetched official model bytes into RAM in the preceding read-only research, computed the bundle/component hashes, and inspected archive members without inference.
- Read primary online model cards, official source/build provenance, license text, and package/dependency metadata.
- Initially downloaded the two named MediaPipe wheels into workspace `backups/l19tmp/`, independently checked their SHA-256 against PyPI and inspected embedded metadata/license entries. The later authorized dependency installation is recorded separately in section 9.
- Added this documentation file only within the backend repository. No requirements, provider code, feature flags, migrations, production systems, or existing user changes were modified. No commit or deployment was performed.

This records research evidence and engineering acceptance limits, not legal advice. The real adapter remains unimplemented by this task and must not be enabled until the outstanding compatibility/offline/resource gates pass.

## 9. Authorized installation, then native-probe hard stop (2026-09-08)

### Measured installation results

| Item | Actual result |
| --- | --- |
| Platform check | Windows 11, `Windows-11-10.0.26200-SP0`, AMD64 |
| Python | CPython 3.12.10, MSC v.1943, 64-bit |
| Separate venv | Workspace `backups/l19tmp/venv-mp101-cp312/` |
| Isolation | `include-system-site-packages = false`; separate `sys.prefix` verified; existing CPython runtime reused without modifying backend site-packages |
| Package install | SUCCESS, exit 0; verified retained Windows MediaPipe 1.0.1 wheel plus `Pillow==12.3.0`; dependencies fetched as binary wheels from `https://pypi.org/simple` |
| Installation controls | `python -I -B -m pip --isolated --disable-pip-version-check install --no-cache-dir --only-binary=:all:`; TEMP/TMP directed to backup temp directory; exact local MediaPipe wheel hash rechecked before install |
| Dependency check | `pip --isolated check`: `No broken requirements found.` |
| Pip report | Workspace `backups/l19tmp/mp101-install-report.json` (321,386 bytes) |
| Report SHA-256 | `215cfd47d840241e95f944adbfac085885c2814c8dae1ef7dc522d751c6bafc5` |
| MediaPipe Python import / DLL load | **NOT RUN** |
| FaceLandmarker creation / detect / close | **NOT RUN** |
| Native wall time / CPU time / peak memory | **NOT MEASURED**; no fabricated zero or passing resource result |
| Synthetic image | Probe code prepared for one uniform RGB `(64,128,192)` image, 512x512; no image creation/inference executed |
| Model retained on disk for this probe | No; the planned download had not happened when execution was stopped. Earlier official bytes were inspected only in RAM |
| Linux runtime available | **false**, main task environment evidence: elevated `wsl --list --quiet` returned exit 0 and empty output; main task also reports Docker absent. These checks were not rerun by this research; no Linux installation or runtime test |

The installation report records the complete selected artifact URLs and SHA-256 values. Installed versions (excluding bootstrap pip) are:

```text
absl-py==2.5.0
certifi==2026.7.22
cffi==2.1.1
contourpy==1.3.3
cycler==0.12.1
flatbuffers==25.12.19
fonttools==4.64.0
kiwisolver==1.5.1
matplotlib==3.11.1
mediapipe==1.0.1
numpy==2.5.3
opencv-contrib-python==5.0.0.93
packaging==26.3
pillow==12.3.0
pycparser==3.0
pyparsing==3.3.2
python-dateutil==2.9.0.post0
six==1.17.0
sounddevice==0.5.6
```

This isolated package set passed only dependency resolution/installation checks. It was not installed into the backend environment. Pip may generate `__pycache__` by byte-compilation during installation; those files do not constitute evidence of MediaPipe import or native execution.

### Static opt-out investigation and precise blocker

1. The exact installed `mediapipe/tasks/python/core/base_options.py` exposes model path/buffer and CPU/GPU delegate. Its conversion to the C options includes host environment, OS, Python version and `certifi` CA-bundle path. `base_options_c.py` includes the corresponding fields and optional application identifiers. Neither inspected file exposes a metrics-disable option. Searches of the installed package's Python/text sources found no supported telemetry opt-out/environment variable. This is a bounded negative finding, not proof that no undocumented native switch exists.
2. The [official task runner source](https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/mediapipe/tasks/cc/core/task_runner.cc) creates a task logger and records session/invocation lifecycle events. This supports treating task creation itself as within the stop boundary, rather than waiting for a portrait input.
3. The [public logging factory](https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/mediapipe/tasks/cc/core/logging/factory/logging_factory.cc) currently returns `TasksDummyLogger`; the [dummy implementation](https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/mediapipe/tasks/cc/core/logging/tasks_dummy_logger.h) drops events. This is a source-build implementation, **not a documented runtime opt-out for the downloaded 1.0.1 wheel**. Exact native-wheel build equivalence to that source was not established. It cannot override the actual wheel's metrics disclosure or prove that its native DLL is telemetry-free. These `master` links are mutable observations, not pinned wheel build attestations.
4. No supported metrics-disabling environment variable was verified for this wheel. Logging verbosity suppression, clearing a CA path, monkey-patching Python sockets, a no-op logger in different source, or a claimed generic sandbox network restriction was not established as sufficient evidence of offline/private native execution. None was used as a workaround. The earlier requirement to verify both a supported opt-out and OS denial was an implementation-review constraint, not an explicit user requirement.
5. No OS-enforced egress-denial boundary for this native child was established or validated. No Windows firewall rule was added, no system networking configuration was changed, and no alternate Linux runtime was installed. The earlier explicit-firewall-permission language was an implementation-review constraint, not a user-authored instruction. This document neither authorizes a firewall change nor introduces a new user approval requirement.

**Concrete gate:** `verified_offline_private_execution = false`. Neither a supported metrics opt-out for the exact wheel nor validated OS egress denial has been established; there is no verified execution arrangement satisfying the privacy/network requirements. This finding does not prescribe a mandatory AND combination or claim that an untested individual mechanism would be sufficient. Native execution remains stopped. Therefore `native_compatibility = NOT_RUN`, `resource_acceptance = NOT_MEASURED`, and `portrait_quality_floor = NOT_TESTED`. Even a future successful uniform-color no-face probe would only establish basic CPU/API operation, not portrait rig quality or multiple-face detection accuracy.

The disposable script `backups/l19tmp/native_probe_mp101.py` was prepared with `num_faces=2`, `output_face_blendshapes=False`, `output_facial_transformation_matrixes=False`, image mode, explicit CPU delegate, pre-load model SHA-256 verification, a 120-second supervisor deadline and a sampled 768 MiB stop threshold. Those are **unexecuted planned controls**, not verified resource confinement. The script exits unconditionally with a blocked message before either worker or supervisor execution. It remains stopped while verified offline/private execution is unavailable; any future resumption must follow the main task's scope and privacy review.

The backup directory is outside the backend Git repository and now contains a local `.gitignore` excluding its artifacts. Weights must remain private backup/provisioning artifacts, never backend Git assets. No model/package downgrade, old-MediaPipe pin, model substitution, real provider, production access, or quality claim was made. The backend `visual_preparation_enabled` default was observed as `False`; this task did not change it. The main task can continue fake-provider/backend work while real-provider and quality acceptance remain blocked.

## 10. Final authorized Linux provider acceptance

This section supersedes historical Windows-only stopped-execution findings, not the artifact/license evidence. Selected provider is **MediaPipe Face Landmarker 1.0.1**, local CPU, `face_landmarker/float16/1`, recipe `portrait_2d_v1`. Real local geometry preparation is implemented and the mandatory native/offline/lifecycle gates passed. This is not authorization to deploy or enable it in production.

### Exact selected artifacts

- Model: `face_landmarker.task`, 3,758,596 bytes; SHA-256 `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
- Numbered source: <https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task>.
- Linux wheel: `mediapipe-1.0.1-py3-none-manylinux_2_28_x86_64.whl`; SHA-256 `121522251afc3c135e4b7b0c341dd5e050ad1ec87631127484f3c389ae385044`.
- Package and model Apache-2.0 evidence remains as established in sections 1–6. Retain applicable licenses, attribution and native/transitive notices for any installer/container redistribution. Private upstream provisioning with digest verification is the chosen method; weights are not browser assets or Git files.

### Verified environment and reproducibility

The explicitly authorized disposable environment at `/var/tmp/l19-phase-b-closure-a1f4c8/` used Linux x86-64, kernel `7.0.0-28-generic`, glibc 2.43 and **CPython 3.14.4**. This proves this environment, not Windows, ARM, musl, GPU or a different Python/runtime. `pip check` returned `No broken requirements found.`

Native package closure, pinned from the executed environment:

```text
absl-py==2.5.0
certifi==2026.7.22
cffi==2.1.1
contourpy==1.3.3
cycler==0.12.1
flatbuffers==25.12.19
fonttools==4.64.0
kiwisolver==1.5.1
matplotlib==3.11.1
mediapipe==1.0.1
numpy==2.5.3
opencv-contrib-python==5.0.0.93
pillow==12.3.0
pycparser==3.0
pyparsing==3.3.2
python-dateutil==2.9.0.post0
six==1.17.0
sounddevice==0.5.6
```

The full executed QA/backend package inventory, including test/framework dependencies, is retained outside Git in `backups/l19-closure/runtime-inventory.json`; exact installation URLs/artifact hashes in `linux-install-report.json`; dependency result in `pip-check.log`. Runtime code uses `MALLOC_ARENA_MAX=2` in its minimal native environment to prevent virtual-arena exhaustion under the unchanged hard address-space ceiling.

Missing OS libraries were downloaded using configured OS package sources and extracted into the private sysroot, not installed globally: libbsd0 0.12.2-2build2; libmd0 1.1.0-2build4; libgl1/libglvnd0/libglx0/libegl1/libgles2 1.7.0-3; libx11-6 2:1.8.13-1; libxau6 1:1.0.11-1build2; libxcb1 1.17.0-2ubuntu1; libxdmcp6 1:1.1.5-2. No linker or production virtualenv reconfiguration occurred. Linux Landlock ABI >=3 and libseccomp are required; inability to establish confinement fails closed.

### Offline and telemetry evidence

Actual initialization and five synthetic portrait preparations succeeded within a transient systemd unit with DynamicUser, PrivateNetwork, RestrictAddressFamilies=AF_UNIX, ProtectHome/ProtectSystem, private temporary storage, NoNewPrivileges, MemoryMax=768M, MemorySwapMax=0, CPUQuota=50% and TasksMax=64. No persistent QA unit was installed. Separate negative tests verify real socket and private-file denial.

Before importing MediaPipe, the child also installs Landlock filesystem restrictions and libseccomp rules denying all socket families/DNS transport and process-control/exec escapes. It receives no API keys, auth/S3 credentials, factual/domain records or storage destination. Local model checksum is verified before native use. No request-time download, remote inference or network exception is allowed.

The wheel's metrics disclosure remains relevant. No supported metrics-disable switch or proof of zero internal telemetry events is claimed. **OS confinement prevents transport**, including initialization-time transport. Confined success establishes that networking is not required for these tested operations; it does not prove an unconstrained wheel would never attempt network or telemetry.

### Real adapter, lifecycle and measured bounds

Final Linux provider/reference/native group: **196 passed, 3 lifecycle cases deselected, zero skipped**. The separate real lifecycle group: **3 passed**, covering full real prepare/preview/activate/viewer/toggle/source-delete/purge, independent lease loss, and source deletion while a real child runs. Factual table snapshots remain unchanged; stale results never publish.

Failure coverage includes native crash/invalid output, deadline/cancellation, memory enforcement, process-group and parent-death termination, private-file/network denial, cleanup and safe orphan startup sweep. A real MediaPipe-mapped child was observed before parent termination. Marker/flock leases preserve live/unregistered directories while reaping only owned abandoned workspaces.

[Phase B report section 4](L19_PHASE_B_REPORT.md#4-portrait-quality-and-resources) records all seven portrait cases. Five pass (frontal, grayscale, glasses, low-resolution and manually selected group subject), two safely require recrop (unusable crop and ambiguous group). Successful preparation: **2.067–2.413 seconds**, peak RSS **205256–205740 KiB**, bundles **393256–548262 bytes**, 484 vertices/882 triangles, 256x256 poster and 512x512 atlas. All 27 neutral/intermediate/blink/mouth envelope combinations validate. No resource ceiling was raised.

This uses actual synthetic portrait pixels and measured eye/lip geometry, without identity matching, persisted recognition embeddings, demographic/expression/factual inference or remote AI. Restrained eyelid deformation is not full photorealistic blink closure. Automated validity does not prove subjective likeness or final Phase C rendering quality.

### Production provisioning contract and cleanup

Phase C must separately provision an isolated visual-worker runtime using the verified package/model pins, validate hashes, preserve applicable licenses/notices and repeat target-host confinement/health smoke checks. Configure `VISUAL_WORKER_PYTHON`, `VISUAL_MODEL_PATH` and optional trusted `VISUAL_NATIVE_LIBRARY_DIR`; use restricted service identity and private temporary storage. Never download a model during a preparation request. The API process does not import MediaPipe or require its native dependencies.

The backend feature defaults remain false. Runtime/model provisioning, worker service deployment, operational alerts and feature enablement belong to the separately authorized Phase C release. Fake provider cannot satisfy capability or preparation availability and is never a production fallback.

The user explicitly resolved the earlier source-archive transfer rejection and authorized private dependencies/test execution. Final server cleanup verified both registered synthetic S3 namespaces contained zero objects/versions and removed the exact QA runtime, including model, wheels, venv, sysroot, code and synthetic files. Credential-free evidence and source/model pins remain locally outside Git. Production application checkout/services/configuration/database were not modified. No deployment, final L19 tag or Phase C work occurred.
