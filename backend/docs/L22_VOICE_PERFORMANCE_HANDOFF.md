# L22 — Preserved Voice Performance & Production GPU Qualification

Deferred roadmap work, **not an L21 correctness acceptance blocker**.
No target GPU has been provisioned or qualified. No performance code from the
uncommitted L21.6 experiment is required by final L21. Preserved Live remains OFF.

## Evidence provenance and limits

The earlier local experiment used NVIDIA GTX 1650 (4096 MiB), torch 2.4.1+cu121,
driver 529.04, the immutable L21 IndicF5/Vocos artifacts and unchanged accepted
settings. Same synthetic/consented QA reference and seed 216; two warm rounds.
CPU regression/build activity overlapped some measurements. These ranges are
observations, **not percentiles, SLAs, concurrency capacity or physical latency**.

Raw evidence remains outside Git in workspace `tmp/l21f`:

- `profile_baseline.py`, `baseline/report.json`: whole-answer baseline.
- `profile_optimized.py`, `optimized/report.json`: sample-local embedding reuse,
  whole answers, sentence/grouping experiments and operator profiling.
- `profile_pipeline.py`, **`real-pipeline-report.json`**: actual GPU/worker/storage/
  renderer/Output integration. Do not substitute `pipe-v2/report.json`, later
  overwritten by the separate CPU replay.
- `profile_overhead.py`, `overhead.json`: CPU/DB/storage replay, no GPU inference.
- `profile_contention.py`, `contention.json`; `analyze_joins.py`, `joins.json`.
- Original code and complete experiment report are archived in
  `tmp/l21-final/experiment-archive`, outside both repositories. Old scripts
  depend on removed experimental APIs; port them into an isolated L22 experiment
  before use. Do not reintroduce them into the L21 production path.

## Whole-answer baseline

First provider audio is available only after the entire waveform is generated.

| Synthetic case | Characters / words | Audio seconds | Warm first-ready / total seconds | RTF |
|---|---:|---:|---:|---:|
| A short Marathi | 13 / 3 | 1.109 | 25.631–26.254 | 23.10–23.67 |
| B Marathi sentence | 29 / 6 | 2.539 | 32.281–32.905 | 12.72–12.96 |
| C three Marathi sentences | 91 / 16 | 8.064 | 53.589–54.029 | 6.65–6.70 |
| D five Marathi sentences | 161 / 27 | 14.325 | 78.043–78.160 | 5.45–5.46 |
| E Marathi-English | 37 / 7 | 2.197 | 30.611–30.665 | 13.93–13.96 |

Artifact verification 1.870 s; first fresh model/Vocos load 72.283 s; cold B
45.085 s. Another fresh load was 25.644 s, cold B 45.416 s. Not startup guarantees.
Cold B acoustic inference consumed 44.752 / 45.085 s (**99.26%**). Reference
preprocessing 56.4 ms, decode 22.5 ms, text preparation 13.9 ms, Vocos 89.8 ms.
Warm preprocessing 31.7–38.9 ms; decode 0.52–1.68 ms; text 0.46–21.96 ms.

GPU utilization reached 99%; driver footprint approximately 2504–2518 MiB,
temperature 67–77 C. Torch baseline live allocation 1,420,161,536 bytes, peak
1,536,887,808, reservation 1,606,418,432. Driver memory includes context/display
and is not equivalent to torch allocation or a minimum VRAM recommendation.
Observed 2,112 attention calls = 48 steps × two CFG predictions × 22 layers.
The installed Windows build reported no flash attention.

Sample-local conditional/null text embedding reuse produced identical PCM for
10 warm comparisons but small/noisy improvements: A 25.305–27.517, B 31.944–32.411,
C 52.944–53.007, D 76.694–77.009, E 29.941–30.737 s. Removed from L21.
No cross-request reference cache, precision/quality reduction or compile change
was introduced. Conditioning is repeated for independent speech chunks.

## Removed chunking experiment

Exact Unicode slices of the **already frozen** answer; never LLM deltas.
Conservative sentence punctuation/titles/initials, long-clause fallback, maximum
32 grouped slices. One in-flight synthesis with playback overlap, no concurrent
GPU inference. Removed chunker, flag, renderer/controller plumbing and cache.

| C grouping | Words | First-ready s | Total synthesis s | Audio s | Simulated waiting gaps s |
|---|---|---:|---:|---:|---|
| sentence | 6 / 5 / 5 | 32.344–33.213 | 102.877–111.525 | 7.957 | 36.51–44.12; 28.26–28.43 |
| short target 8 | 11 / 5 | 43.351–46.069 | 74.292–76.598 | 8.011 | 24.72–25.13 |
| medium target 18 | 16 | 53.569–53.905 | same | 8.064 | none after whole-answer wait |

Sentence-first reduced C mean first-ready about 39%, but roughly doubled work
and introduced very long gaps. This is not a conversational solution on GTX 1650.
Joins concatenated complete PCM; no external trimming/crossfade, accepted internal
IndicF5 crossfade remained 0.10. Joined QA WAVs omit runtime gaps.
RMS −17.90 / −18.41 / −19.71 dBFS; peaks 0.891 / 0.799 / 0.500; endpoint jumps
0.00058 and 0.00858 full scale. These do not certify audible joins/prosody.
New chunk-join human listening was not completed; any L22 reintroduction needs it.

## Integrated and isolated measurements

Whole B first Output PCM 32.915 s, complete synthesis 32.937 s, worker inference
32.125 s. Sentence C first Output PCM 32.872 s, later 67.453 / 97.985 s, total
97.986 s; simulated gaps 32.042 / 27.311 s and playback end 100.183 s.
Persistence took 143 / 55 ms after software drain proofs. No physical audio,
remote brain or real-network WSS timing was measured.

CPU-only replay: admission 40.6–49.1 ms; claim checks per turn 17.6–34.6 ms;
reference DB/read/verify 13.9–18.4 ms; write 2.1–3.8 ms; two reads 7.3–11.1 ms;
verification 8.3–19.6 ms; result read/decode 15.4–33.3 ms. Overlapping wrappers
must not be added. No-inference total 0.596–0.800 s with 250 ms polling.
The CLI idle-poll default remained two seconds.

Single-GPU contention: running preview 78.273 s; later Live queued 78.393 s then
synthesized 32.220 s; queued message 26.296 s. Non-preemptive order was running
preview → Live → message. Existing 90-second Live deadline would fall back for
this queue+inference delay. Priority is not a latency guarantee or fair sharing.

Tensor-level B trace: four explicit CPU→CUDA transfers (886,984 bytes), two
CUDA→CPU (339,312 bytes including returned spectrogram). Separate operator trace:
6,711 aten::to, 218 aten::_to_copy, 4,823 aten::copy_. Not all are physical copies.
No per-turn weight transfer observed. Avoiding an unused returned spectrogram
copy is only a future measured hypothesis.

The first unseeded integration probe failed waveform validation and published
no audio; retained at `tmp/l21f/pipe`. Seeded statistics exclude that failure.
Do not relax output validation or hide stochastic failures to improve numbers.

## L22 qualification plan — no purchasing/provisioning authorization

1. Benchmark **L4 first** using the exact artifact/config/reference matrix.
   This is a proposed first candidate, not a claim that L4 meets requirements.
2. Benchmark **L40S as the fallback candidate** if L4 lacks measured headroom.
   Neither GPU has been measured here; no invented throughput/cost/latency figures.
3. Agree first-audio, gap, p95/p99 and concurrency SLOs before evaluating results.
   Measure warm/cold restart, full-answer distribution, realistic load, VRAM,
   queue delay, cancellation under contention and standard fallback frequency.
4. Compare whole-answer, exact-text chunking/pipelining, bounded reference caching,
   compile/kernel experiments and worker allocation only on measured evidence.
   Preserve/requalify NFE48, CFG1.65, sway−1, speed0.97, crossfade0.10, RMS0.1.
5. Correlate physical Android microphone input → transcription → existing brain
   final answer → worker → WSS → actual speaker start/drain and acoustic barge-in.
   Provider-ready and software AudioContext tests are not substitutes.
6. Repeat human voice/Marathi/code-switch and new join/prosody qualification.
   Preserve one frozen authoritative answer, lifecycle fences, bounded admission,
   no second brain, one complete-drain receipt and one canonical message.

Production Live activation requires explicit L22 performance qualification and
authorization. L21's safe admission ceilings are not production concurrency sizing.
