export const meta = {
  name: 'dockeragent-k8-failure-analysis',
  description: 'Per-instance Haiku failure analysis of DockerAgent K=8 50-repo run, then synthesize one report',
  phases: [
    { title: 'Analyze', detail: 'one Haiku agent per instance: digest -> classify -> write _analysis.md' },
    { title: 'Synthesize', detail: 'aggregate all 50 analyses into FAILURE_ANALYSIS_REPORT.md' },
  ],
}

const BASE = '/Users/john/rat-bench-integration/results/dockeragent/2026-06-06-k8'
const SCRIPT = '/Users/john/rat-bench-integration/results/dockeragent/2026-06-06-k8/_extract_digest.sh'
const INSTANCES = [{"full_name":"BeehiveInnovations/pal-mcp-server","dir":"output/BeehiveInnovations/pal-mcp-server","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.9786,"total_tests":905,"errors":0,"error_breakdown":{}},{"full_name":"D4Vinci/Scrapling","dir":"output/D4Vinci/Scrapling","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.0214,"total_tests":140,"errors":137,"error_breakdown":{"ModuleNotFoundError":94}},{"full_name":"EnableSecurity/wafw00f","dir":"output/EnableSecurity/wafw00f","status":"success","failure_reason":null,"category":"repo2run_weak_test_deficient","pass_rate":0.6667,"total_tests":9,"errors":3,"error_breakdown":{"ModuleNotFoundError":3}},{"full_name":"FoundationAgents/OpenManus","dir":"output/FoundationAgents/OpenManus","status":"error","failure_reason":"build_failed","category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"GoogleCloudPlatform/slurm-gcp","dir":"output/GoogleCloudPlatform/slurm-gcp","status":"error","failure_reason":"no_dockerfile","category":"native_runtime_stress","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"LibreTranslate/LibreTranslate","dir":"output/LibreTranslate/LibreTranslate","status":"success","failure_reason":null,"category":"repo2run_weak_ci_service","pass_rate":1.0,"total_tests":15,"errors":0,"error_breakdown":{}},{"full_name":"MemTensor/MemOS","dir":"output/MemTensor/MemOS","status":"success","failure_reason":null,"category":"winnable_large","pass_rate":0.2662,"total_tests":154,"errors":107,"error_breakdown":{"ModuleNotFoundError":113}},{"full_name":"ModelEngine-Group/nexent","dir":"output/ModelEngine-Group/nexent","status":"error","failure_reason":"no_dockerfile","category":"winnable_large","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"NevaMind-AI/memU-server","dir":"output/NevaMind-AI/memU-server","status":"error","failure_reason":"no_dockerfile","category":"repo2run_weak_ci_service","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"NewFuture/DDNS","dir":"output/NewFuture/DDNS","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.9953,"total_tests":877,"errors":0,"error_breakdown":{"AssertionError":4}},{"full_name":"Nitrokey/pynitrokey","dir":"output/Nitrokey/pynitrokey","status":"success","failure_reason":null,"category":"native_runtime_stress","pass_rate":0.0,"total_tests":1,"errors":1,"error_breakdown":{"ModuleNotFoundError":1}},{"full_name":"Peterande/D-FINE","dir":"output/Peterande/D-FINE","status":"success","failure_reason":null,"category":"native_runtime_stress","pass_rate":0.0,"total_tests":1,"errors":1,"error_breakdown":{"ModuleNotFoundError":1}},{"full_name":"PrimeIntellect-ai/verifiers","dir":"output/PrimeIntellect-ai/verifiers","status":"error","failure_reason":"build_failed","category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"Tecnativa/docker-socket-proxy","dir":"output/Tecnativa/docker-socket-proxy","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"Yelp/dumb-init","dir":"output/Yelp/dumb-init","status":"error","failure_reason":"build_failed","category":"native_runtime_stress","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"aapatre/Automatic-Udemy-Course-Enroller-GET-PAID-UDEMY-COURSES-for-FREE","dir":"output/aapatre/Automatic-Udemy-Course-Enroller-GET-PAID-UDEMY-COURSES-for-FREE","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.8889,"total_tests":27,"errors":0,"error_breakdown":{"OtherError":3}},{"full_name":"aiidateam/aiida-core","dir":"output/aiidateam/aiida-core","status":"success","failure_reason":null,"category":"winnable_large","pass_rate":1.0,"total_tests":0,"errors":0,"error_breakdown":{"TimeoutError":1}},{"full_name":"bruin-data/ingestr","dir":"output/bruin-data/ingestr","status":"error","failure_reason":"build_failed","category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"conor-is-my-name/n8n-autoscaling","dir":"output/conor-is-my-name/n8n-autoscaling","status":"error","failure_reason":"build_failed","category":"repo2run_weak_ci_service","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"copier-org/copier","dir":"output/copier-org/copier","status":"success","failure_reason":null,"category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"dataabc/weibo-crawler","dir":"output/dataabc/weibo-crawler","status":"error","failure_reason":"build_failed","category":"connection_error_stress","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"django-oauth/django-oauth-toolkit","dir":"output/django-oauth/django-oauth-toolkit","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"docling-project/docling","dir":"output/docling-project/docling","status":"error","failure_reason":"build_failed","category":"hard_general","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"epam/ai-dial-sdk","dir":"output/epam/ai-dial-sdk","status":"error","failure_reason":"build_failed","category":"easy_control","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"feast-dev/feast","dir":"output/feast-dev/feast","status":"error","failure_reason":"no_dockerfile","category":"winnable_large","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"frappe/press","dir":"output/frappe/press","status":"error","failure_reason":"no_dockerfile","category":"winnable_large","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"gip-inclusion/les-emplois","dir":"output/gip-inclusion/les-emplois","status":"error","failure_reason":"no_dockerfile","category":"winnable_large","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"google/Xee","dir":"output/google/Xee","status":"success","failure_reason":null,"category":"easy_control","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"jasonxtn/Argus","dir":"output/jasonxtn/Argus","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.0,"total_tests":4,"errors":4,"error_breakdown":{"ModuleNotFoundError":4}},{"full_name":"jhao104/proxy_pool","dir":"output/jhao104/proxy_pool","status":"success","failure_reason":null,"category":"documented_rat_failure","pass_rate":1.0,"total_tests":147,"errors":0,"error_breakdown":{}},{"full_name":"lyuwenyu/RT-DETR","dir":"output/lyuwenyu/RT-DETR","status":"error","failure_reason":"no_dockerfile","category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"microsoft/markitdown","dir":"output/microsoft/markitdown","status":"error","failure_reason":"build_failed","category":"documented_rat_failure","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"nginx-proxy/nginx-proxy","dir":"output/nginx-proxy/nginx-proxy","status":"error","failure_reason":"no_dockerfile","category":"documented_rat_failure","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"nomadkaraoke/karaoke-gen","dir":"output/nomadkaraoke/karaoke-gen","status":"error","failure_reason":"no_dockerfile","category":"winnable_large","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"open-webui/mcpo","dir":"output/open-webui/mcpo","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":1.0,"total_tests":27,"errors":0,"error_breakdown":{}},{"full_name":"pre-commit/pre-commit","dir":"output/pre-commit/pre-commit","status":"success","failure_reason":null,"category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"py2many/py2many","dir":"output/py2many/py2many","status":"success","failure_reason":null,"category":"native_runtime_stress","pass_rate":0.813,"total_tests":1603,"errors":0,"error_breakdown":{"AssertionError":38,"ModuleNotFoundError":1,"OtherError":2,"NameError":2}},{"full_name":"python-websockets/websockets","dir":"output/python-websockets/websockets","status":"success","failure_reason":null,"category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":41,"errors":41,"error_breakdown":{"ModuleNotFoundError":41}},{"full_name":"rayai-labs/agentic-ray","dir":"output/rayai-labs/agentic-ray","status":"error","failure_reason":"no_dockerfile","category":"easy_control","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"resend/resend-python","dir":"output/resend/resend-python","status":"success","failure_reason":null,"category":"easy_control","pass_rate":1.0,"total_tests":429,"errors":0,"error_breakdown":{}},{"full_name":"rq/rq","dir":"output/rq/rq","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.0,"total_tests":31,"errors":31,"error_breakdown":{"ModuleNotFoundError":31}},{"full_name":"scylladb/scylla-cluster-tests","dir":"output/scylladb/scylla-cluster-tests","status":"error","failure_reason":"build_failed","category":"winnable_large","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"sirfz/tesserocr","dir":"output/sirfz/tesserocr","status":"success","failure_reason":null,"category":"native_runtime_stress","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"sooperset/mcp-atlassian","dir":"output/sooperset/mcp-atlassian","status":"success","failure_reason":null,"category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"stlehmann/pyads","dir":"output/stlehmann/pyads","status":"error","failure_reason":"build_failed","category":"easy_control","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"supabase/supabase-py","dir":"output/supabase/supabase-py","status":"error","failure_reason":"no_dockerfile","category":"connection_error_stress","pass_rate":0.0,"total_tests":0,"errors":0,"error_breakdown":{}},{"full_name":"swar/nba_api","dir":"output/swar/nba_api","status":"success","failure_reason":null,"category":"connection_error_stress","pass_rate":0.0,"total_tests":44,"errors":44,"error_breakdown":{"ModuleNotFoundError":43,"OtherError":1}},{"full_name":"unit8co/darts","dir":"output/unit8co/darts","status":"success","failure_reason":null,"category":"repo2run_weak_test_deficient","pass_rate":0.0,"total_tests":1,"errors":1,"error_breakdown":{"ModuleNotFoundError":1}},{"full_name":"yihong0618/bilingual_book_maker","dir":"output/yihong0618/bilingual_book_maker","status":"success","failure_reason":null,"category":"repo2run_weak_test_deficient","pass_rate":0.1429,"total_tests":16,"errors":2,"error_breakdown":{"ModuleNotFoundError":2,"AssertionError":4}},{"full_name":"yutto-dev/yutto","dir":"output/yutto-dev/yutto","status":"success","failure_reason":null,"category":"native_runtime_stress","pass_rate":0.0,"total_tests":17,"errors":17,"error_breakdown":{"ModuleNotFoundError":17}}]

const OUTCOMES = [
  'pass_strong',            // build ok + tests collected + pass_rate >= 0.8
  'pass_partial',           // build ok + 0 < pass_rate < 0.8
  'success_no_tests',       // harness "success" but 0 tests collected -> nothing verified
  'success_tests_all_error',// harness "success" but tests collected and (nearly) all error -> hollow success
  'build_failed',           // docker build of eval image failed
  'no_dockerfile',          // agent never produced a Dockerfile (in-sandbox env config failed)
]
const ROOT_CAUSES = [
  'test_deps_not_installed', 'editable_install_missing', 'no_tests_discovered',
  'deps_installed_correctly', 'dockerfile_synthesis_malformed', 'dockerfile_missing_setup_step',
  'dependency_resolution_conflict', 'system_package_or_apt_failure', 'compiler_or_native_build_failure',
  'network_or_resource_limit', 'step_budget_exhausted', 'uncollectable_tests_blocked_config',
  'service_dependency_required', 'other',
]
const FIXABILITY = [
  'trivial_synthesizer_fix', 'planner_strategy_fix', 'needs_more_steps', 'needs_service_deps',
  'genuinely_hard_repo', 'test_harness_artifact', 'already_working',
]

const WORKER_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    instance: { type: 'string' },
    harness_status: { type: 'string' },
    failure_reason: { type: ['string', 'null'] },
    true_outcome: { type: 'string', enum: OUTCOMES },
    root_cause_category: { type: 'string', enum: ROOT_CAUSES },
    root_cause: { type: 'string', description: '1-3 sentences, specific, evidence-tied' },
    terminal_state: { type: 'string', description: 'env/trajectory state at end: steps used, installed vs missing, last failing action' },
    agent_steps: { type: 'integer', description: 'number of agent ReAct steps taken (0 if unknown)' },
    key_evidence: { type: 'string', description: 'the smoking-gun log/Dockerfile line(s)' },
    takeaway: { type: 'string', description: 'what DockerAgent should do differently' },
    fixability: { type: 'string', enum: FIXABILITY },
    pass_rate: { type: 'number' },
    analysis_file: { type: 'string', description: 'absolute path of the _analysis.md written' },
  },
  required: ['instance', 'true_outcome', 'root_cause_category', 'root_cause', 'terminal_state', 'key_evidence', 'takeaway', 'fixability', 'analysis_file'],
}

function workerPrompt(it) {
  const absdir = `${BASE}/${it.dir}`
  return `You are analyzing ONE instance from a DockerAgent environment-setup benchmark run (RAT "hard" 50-repo subset, K=8 sampling, model deepseek-v4-flash).

DockerAgent's job: autonomously configure a target repo's environment inside a Docker sandbox via a ReAct loop, then emit (a) a Dockerfile and (b) a test script, so the repo's own test suite can be executed and scored. The harness then builds that Dockerfile as an "eval image" and runs pytest.

INSTANCE: ${it.full_name}
OUTPUT DIR (absolute): ${absdir}
Harness status: ${it.status}   failure_reason: ${it.failure_reason}
Benchmark difficulty category: ${it.category}
Pytest aggregate: pass_rate=${it.pass_rate}, total_tests=${it.total_tests}, errors=${it.errors}, error_breakdown=${JSON.stringify(it.error_breakdown)}

== STEP 1 — Gather evidence ==
The run.log can be >1MB. DO NOT cat or Read the whole run.log. Instead run exactly:
  bash "${SCRIPT}" "${absdir}"
That prints a bounded digest: result row, meta, agent-summary (incl. the synthesized Dockerfile and logs.error), the eval_build/Dockerfile that docker build actually used, pytest results, a per-step trajectory overview, the last 3 steps in full, key error signals, and the terminal log tail.
If (and only if) you need more, use targeted commands like:
  grep -nE "PATTERN" "${absdir}/run.log" | tail -40
  sed -n 'START,ENDp' "${absdir}/run.log"
Never read the entire run.log.

== STEP 2 — Determine the TRUE outcome (not just the harness status) ==
IMPORTANT: the harness marks status="success" whenever a Dockerfile builds, EVEN IF every test errors or zero tests were collected. Judge honestly using these classes:
  - pass_strong: build ok + tests collected + pass_rate >= 0.8
  - pass_partial: build ok + 0 < pass_rate < 0.8
  - success_no_tests: status success but 0 tests collected (nothing was actually verified)
  - success_tests_all_error: status success but tests collected and (nearly) all error (typically ModuleNotFoundError) -> the environment is INCOMPLETE ("hollow success")
  - build_failed: docker build of the eval image failed
  - no_dockerfile: the agent never produced a Dockerfile (in-sandbox "Environment Configuration FAILED")

== STEP 3 — Diagnose root cause and trajectory state ==
Establish, from evidence:
  - The specific smoking gun: e.g. a malformed Dockerfile line (dangling backslash, two RUNs merged, missing package arg), a pip/uv ResolutionImpossible, a missing editable install (package not importable), missing test-only extras, tests uncollectable (conftest import error) which blocked config, the agent running out of steps, a required external service (DB/redis/browser/GPU), an apt/system or native-compiler failure, a network/resource limit, etc.
  - State of env/trajectory at termination: how many agent steps ran, what got installed vs what was still missing, what the LAST action attempted was and why it failed.
  - Pick root_cause_category from: ${ROOT_CAUSES.join(', ')}
  - Pick fixability from: ${FIXABILITY.join(', ')}
For "success_tests_all_error" / hollow successes, the usual root cause is test_deps_not_installed or editable_install_missing — verify which by looking at WHAT module is missing (the package itself vs a test/dev dependency) in the pytest ModuleNotFoundError lines.
For build_failed, READ the eval_build/Dockerfile lines in the digest carefully — the bug is often visible there (this is a synthesizer code-gen issue, root_cause_category=dockerfile_synthesis_malformed, fixability=trivial_synthesizer_fix). Distinguish that from genuine dependency/native build failures.
For no_dockerfile, find the last failing setup command before "Environment Configuration FAILED".

== STEP 4 — Write the per-instance analysis file ==
Write a markdown file to EXACTLY: ${absdir}/_analysis.md
Structure:
  # Failure Analysis — ${it.full_name}
  one-line metadata (harness status, true outcome, category, pytest line)
  ## Root cause
  ## Environment / trajectory state at termination   (steps used; installed vs missing; last failing action)
  ## Key evidence   (a fenced code block quoting 2-6 REAL lines from the digest)
  ## Takeaway for DockerAgent
  ## Fixability   (the class + one sentence why)
Keep it tight, concrete, and evidence-based. Quote real lines you saw.

== STEP 5 — Return the structured object ==
Set analysis_file to "${absdir}/_analysis.md". Be skeptical and precise; tie every claim to evidence in the digest. If the harness "succeeded" but tests all errored or none ran, say so plainly.`
}

// ---------- Phase 1: per-instance analysis (Haiku) ----------
phase('Analyze')
const results = await parallel(
  INSTANCES.map((it) => () =>
    agent(workerPrompt(it), {
      label: it.full_name,
      phase: 'Analyze',
      model: 'haiku',
      agentType: 'general-purpose',
      schema: WORKER_SCHEMA,
    })
  )
)
const analyses = results.filter(Boolean)

// ---------- Aggregate (plain JS, deterministic) ----------
function tally(key) {
  const m = {}
  for (const a of analyses) {
    const k = a[key] || 'unknown'
    m[k] = (m[k] || 0) + 1
  }
  return Object.fromEntries(Object.entries(m).sort((x, y) => y[1] - x[1]))
}
const byOutcome = tally('true_outcome')
const byRootCause = tally('root_cause_category')
const byFixability = tally('fixability')

// outcome distribution per benchmark difficulty category
const catIndex = Object.fromEntries(INSTANCES.map((it) => [it.full_name, it.category]))
const byCategory = {}
for (const a of analyses) {
  const c = catIndex[a.instance] || 'unknown'
  byCategory[c] = byCategory[c] || {}
  byCategory[c][a.true_outcome] = (byCategory[c][a.true_outcome] || 0) + 1
}
const aggregates = {
  total: analyses.length,
  missing: INSTANCES.length - analyses.length,
  byOutcome, byRootCause, byFixability, byCategory,
}
log(`Analyzed ${analyses.length}/${INSTANCES.length}. Outcomes: ${JSON.stringify(byOutcome)}`)

// ---------- Phase 2: synthesis (inherits main model) ----------
phase('Synthesize')
const compact = analyses.map((a) => ({
  instance: a.instance,
  true_outcome: a.true_outcome,
  harness_status: a.harness_status,
  failure_reason: a.failure_reason,
  category: catIndex[a.instance],
  pass_rate: a.pass_rate,
  agent_steps: a.agent_steps,
  root_cause_category: a.root_cause_category,
  fixability: a.fixability,
  root_cause: a.root_cause,
  terminal_state: a.terminal_state,
  key_evidence: a.key_evidence,
  takeaway: a.takeaway,
}))

const synthPrompt = `You are the lead analyst writing the FINAL consolidated report for a DockerAgent environment-setup benchmark run (RAT "hard" 50-repo subset, K=8, model deepseek-v4-flash). DockerAgent autonomously configures each repo's environment in a Docker sandbox, then emits a Dockerfile + test script; the harness builds that image and runs the repo's pytest suite.

You are given (1) precomputed aggregates and (2) one structured analysis per instance produced by 50 per-instance agents. Per-instance markdown files already exist at ${BASE}/output/<org>/<repo>/_analysis.md — you MAY read a few with Read/Bash if you need a direct quote, but do not re-read run.logs.

PRECOMPUTED AGGREGATES (authoritative counts — use these numbers, do not recount by hand):
${JSON.stringify(aggregates, null, 2)}

PER-INSTANCE ANALYSES (JSON array):
${JSON.stringify(compact, null, 2)}

Write a rigorous, decision-useful report. Lead with the most important finding: the harness "56% success (28/50)" is a BUILD-success metric, not an environment-correctness metric — many "successes" have pass_rate 0 because tests all error (ModuleNotFoundError = incomplete env) or because zero tests were collected (nothing verified). Compute and feature the honest funnel: produced a Dockerfile -> image built -> tests collected -> tests actually pass. State how many repos reached each stage and the genuine "environment actually works" count (pass_strong + pass_partial).

Required sections:
1. Executive summary (5-10 bullets): the headline funnel, the real success rate vs the reported 56%, the dominant failure modes, and the single highest-leverage fix.
2. The success illusion — hollow successes & no-test successes: list the instances, explain why build-success != working-env, and what the harness/agent should assert instead (e.g. import the package + collect tests as a gate before declaring success).
3. Failure taxonomy: a table of root_cause_category -> count -> mapped instances, ordered by frequency.
4. Deep dives by root cause (one subsection per significant category): mechanism, representative instances with a quoted smoking-gun line, and the concrete fix. Explicitly cover: dockerfile synthesis bugs (e.g. FoundationAgents/OpenManus malformed RUN line), the no_dockerfile/env-config-failed cluster, dependency-resolution conflicts, missing editable/test-dep installs behind hollow successes, and any service/native/system cases.
5. Outcome by difficulty category (use byCategory): which categories DockerAgent handles vs not.
6. Cross-cutting patterns: connect build_failed and no_dockerfile (both stem from in-sandbox "Environment Configuration FAILED"; build_failed additionally emitted a fallback Dockerfile that was malformed/incomplete). Note the verification-bundle gap ("No accepted Verification Bundle test commands were found").
7. Prioritized recommendations: a ranked table — each row = recommendation, # instances it would move forward, target code area (use repo knowledge: src/synthesizer.py for Dockerfile codegen/command-order bugs, src/planner.py for step-budget/verification-bundle/strategy, src/verification_bundle.py for test-command acceptance, src/sandbox.py for retries), and effort. Rank by impact (instances recovered).
8. Appendix: a full 50-row table — instance | category | harness status | TRUE outcome | pass_rate | root cause | fixability. Link each to its ./output/<org>/<repo>/_analysis.md.

Be precise, quantitative, and skeptical. Prefer concrete instance names and quoted evidence over generalities. Use GitHub-flavored markdown.

WRITE the report to EXACTLY: ${BASE}/FAILURE_ANALYSIS_REPORT.md
Then return a ~12-line plain-text executive summary (the funnel + top 3 recommendations) as your final message.`

const execSummary = await agent(synthPrompt, {
  label: 'synthesize-report',
  phase: 'Synthesize',
  agentType: 'general-purpose',
})

return { aggregates, analyses, execSummary }
