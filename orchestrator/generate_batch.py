"""Generate config-batch.json: 15 small deterministic QA-detector tasks over
babylon-cinema game-project manifests (ROADMAP epic 26 "disconnected parts auditor",
decomposed). Each task = one detector .ts + one vitest spec. All workers = Opus.
"""
import json

IB = "integration/agent-pipeline-test"

EX = ("examples/game-engine/pharmacy-help-pt-a2/project.json and "
      "examples/game-engine/coffee-order-en-a1/project.json")

COMMON = (
    f"\n\nFIRST read {EX} to learn the REAL JSON shape and the exact field names you "
    "need (scenes[] and their id, entities[].components[] with a kind and config, "
    "portals[], oralTaskGraphs[].nodes[], teacherActivities[], prefabs[]). Match the "
    "detector to the ACTUAL shape you observe. If the specific concept does not exist in "
    "the shape, still implement the detector generically and ensure BOTH example projects "
    "yield ZERO findings.\n\nHARD RULES: TypeScript only. NodeNext ESM, import specifiers "
    "end in .js (the test is in test/qa/, the script in scripts/qa/, so import the script "
    "as ../../scripts/qa/<name>.js). Comments explain only WHY. No new dependencies. "
    "Surgical: create ONLY the two files named. The CLI form `tsx scripts/qa/<name>.ts "
    "<project.json>` must print a JSON summary and process.exit(1) when findings are "
    "non-empty, else 0. The vitest spec MUST: (a) load both example projects and assert "
    "the detector returns ZERO findings for each, and (b) build an inline fixture that "
    "triggers exactly one finding and assert it is detected. VERIFY by running the exact "
    "verify command and ensure it passes. If genuinely blocked, STOP and report; never "
    "edit unrelated files.")

# (id, human title, the detection rule + exported function signature, fixture hint)
DETECTORS = [
    ("quest-without-npc", "oral-task node references an undefined NPC",
     "export function findQuestsWithoutNpc(project): { missing: Array<{node:string;npcId:string}> } — for every oralTaskGraphs[].nodes[] node that carries an npcId, flag it if that npcId is not defined by any dialogue.npc component.",
     "fixture: an oralTaskGraph node with npcId 'ghost' and no dialogue.npc defining 'ghost' -> one finding."),
    ("npc-without-task", "defined NPC never referenced by any oral task",
     "export function findNpcsWithoutTask(project): { unused: string[] } — dialogue.npc config.npcId values never referenced by any oralTaskGraphs node npcId.",
     "fixture: define dialogue.npc 'baker', no node references it -> unused ['baker']."),
    ("duplicate-scene-ids", "two scenes share the same id",
     "export function findDuplicateSceneIds(project): { duplicates: string[] } — scene ids appearing more than once.",
     "fixture: two scenes both id 'home' -> duplicates ['home']."),
    ("duplicate-npc-ids", "two dialogue.npc components share a npcId",
     "export function findDuplicateNpcIds(project): { duplicates: string[] } — config.npcId values appearing on more than one dialogue.npc component.",
     "fixture: two dialogue.npc with npcId 'clerk' -> duplicates ['clerk']."),
    ("empty-scenes", "scene has no entities",
     "export function findEmptyScenes(project): { empty: string[] } — scene ids whose entities array is missing or empty.",
     "fixture: a scene 'void' with entities [] -> empty ['void']."),
    ("npc-empty-fallback", "dialogue.npc has no fallback lines",
     "export function findNpcsWithEmptyFallback(project): { offenders: string[] } — dialogue.npc config.npcId whose config.fallbackLines is missing or empty.",
     "fixture: dialogue.npc 'mute' with fallbackLines [] -> offenders ['mute']."),
    ("scene-missing-displayname", "scene lacks a displayName",
     "export function findScenesMissingDisplayName(project): { offenders: string[] } — scene ids with missing/empty displayName.",
     "fixture: scene 'x' without displayName -> offenders ['x']."),
    ("portal-missing-target", "portal has no destination field",
     "export function findPortalsMissingTarget(project): { offenders: Array<{fromScene:string;index:number}> } — portals whose destination scene id field is missing or empty.",
     "fixture: a scene 'home' with one portal lacking a destination -> one offender."),
    ("unused-prefabs", "prefab defined but never instantiated",
     "export function findUnusedPrefabs(project): { unused: string[] } — prefabs[] ids never referenced by any entity (discover how entities reference prefabs).",
     "fixture: prefab 'crate' defined, no entity references it -> unused ['crate']."),
    ("component-missing-kind", "component without a kind",
     "export function findComponentsMissingKind(project): { offenders: Array<{scene:string;entityIndex:number}> } — components missing a kind field.",
     "fixture: an entity with a component lacking kind -> one offender."),
    ("invalid-cefr-level", "CEFR level value out of A1..C2",
     "export function findInvalidCefrLevels(project): { offenders: Array<{where:string;level:string}> } — any 'level' field whose value is not one of A1,A2,B1,B2,C1,C2 (case-insensitive).",
     "fixture: a teacherActivity with level 'Z9' -> one offender."),
    ("missing-required-fields", "project missing required top-level fields",
     "export function findMissingRequiredFields(project): { missing: string[] } — of ['id','scenes'] (confirm real required keys from examples) those absent at the top level.",
     "fixture: an object {scenes:[]} with no id -> missing ['id']."),
    ("activity-without-scene", "activity references a scene that does not exist",
     "export function findActivitiesWithoutScene(project): { offenders: Array<{activity:string;sceneId:string}> } — teacherActivities referencing a sceneId not present in scenes (discover the real field linking activity->scene).",
     "fixture: an activity referencing sceneId 'nowhere' not in scenes -> one offender."),
    ("dangling-task-edges", "oral-task node points to a missing next node",
     "export function findDanglingTaskEdges(project): { offenders: Array<{graph:string;from:string;to:string}> } — within each oralTaskGraph, a node edge/next referencing a node id absent from that graph (discover the real edge field).",
     "fixture: a graph with node 'a' whose next is 'zzz' (no such node) -> one offender."),
    ("orphan-items", "action references an item that is not defined",
     "export function findOrphanItems(project): { missing: string[] } — itemId values referenced anywhere that are not defined in the project's item list (discover where items are defined; if no item list exists, return empty for real examples).",
     "fixture: an action referencing itemId 'sword' with no such item defined -> missing ['sword']."),
]

tasks = []
for tid, title, rule, fixture in DETECTORS:
    script = f"scripts/qa/{tid}.ts"
    spec = f"test/qa/{tid}.spec.ts"
    verify = f"npx vitest run {spec}"
    prompt = (f"Create a deterministic detector: {title}. {rule} Be defensive — tolerate "
              f"missing/wrong-typed fields, never throw.{COMMON}\n\nTest {fixture}")
    tasks.append({
        "id": tid, "worker_model": "claude", "max_iters": 12, "worker_timeout_s": 300,
        "commit": f"feat(qa): detector — {title}",
        "allowed_files": [script, spec], "verify_cmd": verify, "spec": prompt,
    })

cfg = {"integration_branch": IB, "max_concurrent": 15,
       "global_deadline_s": 5400, "pool_sids": [], "tasks": tasks}

_REQUIRED_TASK_FIELDS = ("id", "worker_model", "commit", "allowed_files", "verify_cmd", "spec")


def _validate_cfg(cfg):
    for _req in ("integration_branch", "tasks"):
        if _req not in cfg:
            raise ValueError(f"config missing required field: {_req!r}")
    if not isinstance(cfg["tasks"], list):
        raise ValueError("config 'tasks' must be a list")
    ids = [t["id"] for t in cfg["tasks"] if "id" in t and t["id"] is not None]
    if len(ids) != len(set(ids)):
        dupes = sorted(set(x for x in ids if ids.count(x) > 1))
        raise ValueError(f"duplicate task IDs: {dupes}")
    for t in cfg["tasks"]:
        missing = [f for f in _REQUIRED_TASK_FIELDS if f not in t or t[f] is None]
        if missing:
            raise ValueError(f"task {t.get('id','?')} missing fields: {missing}")


_validate_cfg(cfg)
json.dump(cfg, open("config-batch.json", "w"), indent=2)
print(f"wrote config-batch.json with {len(tasks)} detector tasks")
