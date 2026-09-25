"""Coordinator/subagent context passing: demonstrates why attribution failures
are a context-passing bug, not a prompting bug.

A synthesis agent cannot cite a source it was never given. If a coordinator
strips metadata before handing findings downstream, no amount of prompt
engineering on the synthesis agent will recover the citation.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields

MODEL = "claude-opus-5"

# "Task" is the legacy name for this tool; renamed "Agent" in Claude Code
# v2.1.63. Both remain valid.
SPAWN_TOOLS = ("Agent", "Task")


class SpawnNotAllowedError(Exception):
    """Raised when a coordinator has subagents defined but no tool grant to spawn them."""


class NoFindingsError(Exception):
    """Raised when there is nothing to synthesise a report from."""


# ---------------------------------------------------------------------------
# Subagent definitions. An AgentDefinition carries exactly three things:
#   description -> what the coordinator reads to select this agent
#   prompt      -> the subagent's system prompt (field is `prompt`, not
#                  `systemPrompt` - that name belongs to the top-level query)
#   tools       -> the scoped toolset; the agent cannot reach past it
# The agent's name is its key in options["agents"], not a field on the object.
# ---------------------------------------------------------------------------

WEB_SEARCH_AGENT = {
    "description": (
        "Searches the web for current information and returns results with "
        "source URLs and titles"
    ),
    "prompt": (
        "Search for information on the given topic. Return each finding as JSON "
        "with fields: claim, sourceUrl, sourceTitle, retrievedDate, confidence. "
        "Never return a claim without a sourceUrl."
    ),
    "tools": ["WebSearch"],
}

DOC_ANALYSIS_AGENT = {
    "description": "Analyses documents and returns findings with page references",
    "prompt": (
        "Analyse the provided documents. Return each finding as JSON with fields: "
        "claim, documentName, pageNumber, section, confidence. "
        "Never return a claim without a documentName and pageNumber."
    ),
    "tools": ["Read", "Grep"],
}

SYNTHESIS_AGENT = {
    "description": "Merges structured findings into a report where every claim is cited",
    "prompt": (
        "You are given structured findings as JSON. Write a report in which EVERY "
        "factual claim carries a citation drawn from the metadata on its finding "
        "(sourceUrl, or documentName + pageNumber). If a finding lacks the "
        "metadata needed to cite it, say so explicitly rather than stating it bare."
    ),
    # No tools: synthesis reasons over what it was handed, it does not go fetch.
    "tools": [],
}


def describeAgent(name: str, definition: dict) -> None:
    print(f"  agents['{name}']")
    print(f"    description -> {definition['description']}")
    print(f"    prompt      -> {definition['prompt'][:72]}...")
    print(f"    tools       -> {definition['tools'] or '(none - reasons over passed context only)'}")


def buildCoordinatorOptions(includeSpawnTool: bool = True) -> dict:
    """Builds options for query(). Agent/Task in allowedTools is the hard gate:
    without it, options["agents"] is dead configuration - defined, selectable,
    and unreachable."""
    allowedTools = ["Read"]
    if includeSpawnTool:
        allowedTools.insert(0, "Agent")

    agents = {
        "web-search": WEB_SEARCH_AGENT,
        "doc-analysis": DOC_ANALYSIS_AGENT,
        "synthesis": SYNTHESIS_AGENT,
    }

    return {
        "systemPrompt": (
            "You coordinate research by delegating to specialist subagents and "
            "synthesising their findings. Pass each subagent's COMPLETE structured "
            "output downstream - never strip metadata."
        ),
        "model": MODEL,
        "allowedTools": allowedTools,
        "agents": agents,
    }


def assertSpawnAllowed(options: dict) -> None:
    """Fails fast if the coordinator has no way to invoke its subagents."""
    allowedTools = options["allowedTools"]
    if not any(tool in allowedTools for tool in SPAWN_TOOLS):
        raise SpawnNotAllowedError(
            f"None of {SPAWN_TOOLS} present in allowedTools={allowedTools}; "
            f"the {len(options['agents'])} agent(s) under options['agents'] "
            f"({list(options['agents'])}) are unreachable."
        )


# ---------------------------------------------------------------------------
# Structured contract: content separated from attribution.
#
# A citation is built from one of these recipes, satisfied only when every
# field in `requires` is present. Written as data rather than if-statements so
# the requirement can be printed and inspected, not just read out of control
# flow.
# ---------------------------------------------------------------------------

CONTENT_FIELDS = {"claim"}
METADATA_FIELDS = {
    "sourceUrl", "sourceTitle", "documentName", "pageNumber",
    "confidence", "retrievedBy", "retrievedDate",
}

CITATION_RECIPES = [
    {
        "name": "web",
        "requires": ["sourceUrl"],
        "optional": ["sourceTitle"],
        "format": lambda f: f"[{f.sourceTitle or 'source'}]({f.sourceUrl})",
    },
    {
        "name": "document",
        "requires": ["documentName", "pageNumber"],
        "optional": [],
        "format": lambda f: f"{f.documentName}, p.{f.pageNumber}",
    },
]

# Fields that qualify a claim rather than locate it - useful downstream, but
# their absence never blocks a citation.
SUPPORTING_FIELDS = ["confidence", "retrievedBy", "retrievedDate"]


@dataclass
class Finding:
    claim: str
    sourceUrl: str | None
    sourceTitle: str | None
    documentName: str | None
    pageNumber: int | None
    confidence: str          # "high" | "medium" | "low"
    retrievedBy: str         # which subagent produced this
    retrievedDate: str

    def has(self, fieldName: str) -> bool:
        value = getattr(self, fieldName)
        return value is not None and value != ""

    def citation(self, trace: bool = False) -> str | None:
        """Tries each recipe in order; the first fully-satisfied one wins.
        Returns None if no recipe is satisfied - a data problem, never a
        wording problem."""
        for recipe in CITATION_RECIPES:
            missing = [r for r in recipe["requires"] if not self.has(r)]
            if trace:
                verdict = "SATISFIED" if not missing else f"missing {missing}"
                print(f"        recipe '{recipe['name']}' requires {recipe['requires']} -> {verdict}")
            if missing:
                continue
            return recipe["format"](self)
        if trace:
            print("        no recipe satisfied -> citation() returns None")
        return None


@dataclass
class ResearchOutput:
    findings: list[Finding]
    query: str
    timestamp: str


def describeSchema() -> None:
    names = [f.name for f in fields(Finding)]
    content = [n for n in names if n in CONTENT_FIELDS]
    meta = [n for n in names if n in METADATA_FIELDS]
    print(f"  Finding has {len(names)} fields:")
    print(f"    content  ({len(content)}) -> {content}")
    print(f"    metadata ({len(meta)}) -> {meta}")


def describeRequirements() -> None:
    print("  A citation needs ONE recipe fully satisfied:")
    for recipe in CITATION_RECIPES:
        req = " AND ".join(recipe["requires"])
        print(f"    '{recipe['name']:8}' requires: {req}")
    print("  Remaining metadata fields are supporting, not required:")
    for name in SUPPORTING_FIELDS:
        print(f"    {name}")


def showCitationBuild(finding: Finding) -> None:
    print(f"\n    claim        : {finding.claim}")
    print(f"    retrievedBy  : {finding.retrievedBy}")
    filled = sorted(n for n in METADATA_FIELDS if finding.has(n))
    print(f"    metadata present: {filled}")
    result = finding.citation(trace=True)
    print(f"    result -> {result!r}")


# ---------------------------------------------------------------------------
# Parallel spawn. The parallelism lives in one model turn: the coordinator
# emits both Agent tool_use blocks in a single assistant message and the SDK
# runs them concurrently - no asyncio.gather in application code.
# ---------------------------------------------------------------------------

COORDINATOR_PROMPT = (
    "Research the topic. Invoke the web-search and doc-analysis subagents in "
    "PARALLEL - emit both Agent tool calls in a single response, do not wait for "
    "one before starting the other. When both return, pass their COMPLETE "
    "structured findings, every metadata field intact, to the synthesis subagent."
)


def runFakeSubagent(agentName: str, seconds: float) -> ResearchOutput:
    """Stands in for a real subagent run; the sleep is real so wall-clock
    measurements below reflect actual elapsed time."""
    print(f"       [{agentName}] running (~{seconds}s)...")
    time.sleep(seconds)
    output = FAKE_RESULTS[agentName]
    print(f"       [{agentName}] returned {len(output.findings)} findings")
    return output


def runCoordinatorTurn(assistantBlocks: list[dict]) -> dict[str, ResearchOutput]:
    """One assistant turn. Multiple tool_use blocks in the same message run concurrently."""
    toolUses = [b for b in assistantBlocks if b["type"] == "tool_use"]
    print(f"  assistant message carries {len(toolUses)} tool_use block(s)")

    start = time.time()
    with ThreadPoolExecutor(max_workers=len(toolUses)) as pool:
        futures = {
            block["input"]["subagent_type"]: pool.submit(
                runFakeSubagent,
                block["input"]["subagent_type"],
                FAKE_DURATIONS[block["input"]["subagent_type"]],
            )
            for block in toolUses
        }
        results = {name: future.result() for name, future in futures.items()}
    print(f"  turn wall clock: {time.time() - start:.2f}s")
    return results


def compareLatency() -> None:
    """Same two subagents, sequential vs parallel, actually timed."""
    web, docs = "web-search", "doc-analysis"

    start = time.time()
    runFakeSubagent(web, FAKE_DURATIONS[web])
    runFakeSubagent(docs, FAKE_DURATIONS[docs])
    sequentialSeconds = time.time() - start
    print(f"  sequential total: {sequentialSeconds:.2f}s")

    start = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda name: runFakeSubagent(name, FAKE_DURATIONS[name]), [web, docs]))
    parallelSeconds = time.time() - start
    print(f"  parallel total:   {parallelSeconds:.2f}s")
    print(f"  saved {sequentialSeconds - parallelSeconds:.2f}s - valid only because the two "
          f"tasks are independent.")


# ---------------------------------------------------------------------------
# Passing findings to the synthesis subagent. `lossy=True` reproduces the bug
# (summarising findings down to claim strings); `lossy=False` is the fix
# (complete structured objects, verbatim).
# ---------------------------------------------------------------------------

def buildSynthesisPrompt(web: ResearchOutput, docs: ResearchOutput, lossy: bool) -> str:
    if not web.findings and not docs.findings:
        raise NoFindingsError("buildSynthesisPrompt: no findings to synthesise")

    if lossy:
        webBlock = json.dumps([f.claim for f in web.findings], indent=2)
        docsBlock = json.dumps([f.claim for f in docs.findings], indent=2)
    else:
        webBlock = json.dumps([asdict(f) for f in web.findings], indent=2)
        docsBlock = json.dumps([asdict(f) for f in docs.findings], indent=2)

    return (
        "Synthesise the following research findings into a coherent report. "
        "Every claim MUST include a citation with source URL or document + page number.\n\n"
        f"Web search findings ({len(web.findings)} findings, retrievedBy=web-search):\n"
        f"{webBlock}\n\n"
        f"Document analysis findings ({len(docs.findings)} findings, retrievedBy=doc-analysis):\n"
        f"{docsBlock}\n\n"
        "Output a report where every factual claim links to its source."
    )


def synthesise(prompt: str) -> str:
    """Stands in for the synthesis subagent: it can only cite what the prompt
    actually contains. No prompt wording changes this - the information is
    either there or it is not."""
    payload = prompt[prompt.index("Web search findings"):]
    hasMetadata = '"sourceUrl"' in payload or '"documentName"' in payload

    claims = []
    for line in payload.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith('"claim": "'):
            claims.append(line[len('"claim": "'):-1])
        elif line.startswith('"') and line.endswith('"') and ":" not in line:
            claims.append(line.strip('"'))

    reportLines = ["# Research report", ""]
    for claim in claims:
        match = next((f for f in ALL_FINDINGS if f.claim == claim), None) if hasMetadata else None
        cite = match.citation() if match else None
        reportLines.append(f"- {claim} ({cite})" if cite else f"- {claim}")
    return "\n".join(reportLines)


def verifyCitations(report: str, findings: list[Finding]) -> list[str]:
    """Walks the report claim by claim and checks a citation is present.
    Uncited claims should be traced back to whether metadata was in the
    input, not blamed on synthesis-prompt wording."""
    uncited = []
    for finding in findings:
        if finding.claim not in report:
            continue
        line = next(l for l in report.splitlines() if finding.claim in l)
        cite = finding.citation()
        if not (cite is not None and cite in line):
            uncited.append(finding.claim)
    return uncited


def diagnose(uncited: list[str], synthesisPrompt: str) -> None:
    if not uncited:
        print("  all claims attributable - context passing preserved the metadata")
        return
    metadataInPrompt = '"sourceUrl"' in synthesisPrompt or '"documentName"' in synthesisPrompt
    print(f"  {len(uncited)} uncited claim(s); metadata present in synthesis input: {metadataInPrompt}")
    if not metadataInPrompt:
        print("  root cause: coordinator dropped metadata before synthesis - not a synthesis-prompt bug.")


# ---------------------------------------------------------------------------
# Fixture data standing in for real subagent output.
# ---------------------------------------------------------------------------

WEB_FINDINGS = [
    Finding("Global solar capacity passed 1.6 TW in 2024",
            "https://example.com/solar-2024", "Solar Outlook 2024", None, None,
            "high", "web-search", "2026-09-13"),
    Finding("Offshore wind costs fell 12% year over year",
            "https://example.com/wind-costs", "Wind Cost Index", None, None,
            "medium", "web-search", "2026-09-13"),
]
DOC_FINDINGS = [
    Finding("Internal pilot achieved 34% grid-storage round-trip efficiency",
            None, None, "storage_pilot_report.pdf", 12, "high", "doc-analysis", "2026-09-13"),
    Finding("Procurement lead time for turbines averages 14 months",
            None, None, "supply_chain_review.pdf", 41, "medium", "doc-analysis", "2026-09-13"),
]
ALL_FINDINGS = WEB_FINDINGS + DOC_FINDINGS

FAKE_RESULTS = {
    "web-search": ResearchOutput(WEB_FINDINGS, "renewable energy", "2026-09-13T10:00:00Z"),
    "doc-analysis": ResearchOutput(DOC_FINDINGS, "renewable energy", "2026-09-13T10:00:00Z"),
}
FAKE_DURATIONS = {"web-search": 0.8, "doc-analysis": 0.5}


def main() -> None:
    print("== coordinator spawn gate ==")
    try:
        assertSpawnAllowed(buildCoordinatorOptions(includeSpawnTool=False))
    except SpawnNotAllowedError as error:
        print(f"  caught expected error: {error}")

    options = buildCoordinatorOptions(includeSpawnTool=True)
    assertSpawnAllowed(options)
    print(f"  allowedTools -> {options['allowedTools']}")

    print("\n== subagent definitions ==")
    for name in options["agents"]:
        describeAgent(name, options["agents"][name])

    print("\n== structured finding schema ==")
    describeSchema()
    describeRequirements()
    print("\n  citation build - web finding (sourceUrl wins):")
    showCitationBuild(WEB_FINDINGS[0])
    print("\n  citation build - document finding (documentName + pageNumber wins):")
    showCitationBuild(DOC_FINDINGS[0])

    print("\n== passing findings to synthesis: lossy vs full ==")
    webOut, docOut = FAKE_RESULTS["web-search"], FAKE_RESULTS["doc-analysis"]

    lossyPrompt = buildSynthesisPrompt(webOut, docOut, lossy=True)
    lossyReport = synthesise(lossyPrompt)
    uncitedLossy = verifyCitations(lossyReport, ALL_FINDINGS)
    diagnose(uncitedLossy, lossyPrompt)

    fullPrompt = buildSynthesisPrompt(webOut, docOut, lossy=False)
    fullReport = synthesise(fullPrompt)
    uncitedFull = verifyCitations(fullReport, ALL_FINDINGS)
    diagnose(uncitedFull, fullPrompt)

    print("\n== parallel subagent spawn ==")
    compareLatency()
    turn = [
        {"type": "tool_use", "id": "toolu_01", "name": "Agent",
         "input": {"subagent_type": "web-search", "prompt": "Search renewable energy trends"}},
        {"type": "tool_use", "id": "toolu_02", "name": "Agent",
         "input": {"subagent_type": "doc-analysis", "prompt": "Analyse the internal PDFs"}},
    ]
    results = runCoordinatorTurn(turn)
    refactoredReport = synthesise(
        buildSynthesisPrompt(results["web-search"], results["doc-analysis"], lossy=False)
    )

    print("\n== summary ==")
    print(f"  lossy path uncited claims      -> {len(uncitedLossy)} of {len(ALL_FINDINGS)}")
    print(f"  full path uncited claims       -> {len(uncitedFull)} of {len(ALL_FINDINGS)}")
    print(f"  parallel refactor output moved -> {refactoredReport != fullReport}")


if __name__ == "__main__":
    main()
