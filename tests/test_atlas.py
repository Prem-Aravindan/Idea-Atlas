import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from atlas import brief as B  # noqa: E402
from atlas import engine as E  # noqa: E402
from atlas import exporters as X  # noqa: E402
from atlas import search as S  # noqa: E402
from atlas import textparse as tp  # noqa: E402
from atlas.db import Database  # noqa: E402

NOTE = """@thesis brainstorm after reading Kipf & Welling (Kipf, 2017)
Graph Neural Networks (GNNs) are a family of models that learn over graph structure.
Over-smoothing is when node embeddings become indistinguishable after many layers.
Message passing depends on node degree distribution.
What if we use residual connections to reduce over-smoothing?
Why does attention help on heterophilous graphs?
results show 4.2% accuracy gain with DropEdge on Cora
I think the benchmark split might leak labels #todo
Methods:
- contrastive pretraining
- DropEdge
Q: is DropEdge an example of data augmentation?
Decided: we will use PyTorch Geometric for the experiments.
Risk: compute budget is tight for large graphs
see https://arxiv.org/abs/1609.02907
"""


def kinds(parsed):
    return {s.text: s.kind for s in parsed.segments}


class ParserTests(unittest.TestCase):
    def test_kinds_prefixes_and_headings(self):
        k = kinds(tp.parse(NOTE))
        self.assertEqual(k["Why does attention help on heterophilous graphs?"], "question")
        self.assertEqual(k["What if we use residual connections to reduce over-smoothing?"], "idea")
        self.assertEqual(k["contrastive pretraining"], "method")
        self.assertEqual(k["we will use PyTorch Geometric for the experiments."], "decision")
        self.assertEqual(k["compute budget is tight for large graphs"], "risk")
        self.assertEqual(k["I think the benchmark split might leak labels"], "task")
        self.assertEqual(k["see https://arxiv.org/abs/1609.02907"], "reference")

    def test_definitions_aliases_relations(self):
        p = tp.parse(NOTE)
        self.assertIn(("Graph Neural Networks", "GNNs"), p.aliases)
        self.assertIn("Over-smoothing", [d[0] for d in p.definitions])
        rels = [(r.left, r.type, r.right) for s in p.segments for r in s.relations]
        self.assertIn(("Message passing", "depends on", "node degree distribution"), rels)

    def test_hedge_and_handles(self):
        p = tp.parse(NOTE)
        self.assertEqual(p.handles, ["thesis"])
        seg = next(s for s in p.segments if "leak labels" in s.text)
        self.assertEqual(seg.confidence, "low")

    def test_unstructured_prose_without_prefixes(self):
        k = kinds(tp.parse(
            "so after the meeting, our retrieval pipeline is too slow when the corpus gets big. she thinks caching "
            "the embeddings could fix most of it, but I'm not sure that helps with the reranking step. we tried "
            "batching and it gave like 30% speedup. not clear if it scales. maybe we should look at approximate "
            "search instead. also need to write the related work section before friday."))
        self.assertEqual(len(k), 7)
        self.assertEqual(sorted(k.values()), sorted(["risk", "solution", "question", "evidence", "question", "idea", "task"]))

    def test_pasted_explainer_tables_acronyms_lists(self):
        p = tp.parse("Plain-English explanation\n\nDSL = Domain-Specific Language\n\n"
                     "Layer\tPurpose\nETN (Edge Twin Node)\tKeeps sensitive data locally.\n\n"
                     "“Automatic coupling to simulators and data sources”\n\n"
                     "Traditionally, engineers manually integrate:\n\ndatabases,\nsensors,\n\n"
                     "This helps people at risk during a heatwave.")
        self.assertIn(("Domain-Specific Language", "DSL"), p.aliases)
        self.assertIn(("Edge Twin Node", "ETN"), p.aliases)
        self.assertIn(("Edge Twin Node", "Keeps sensitive data locally."), [(t, d) for t, d, _ in p.definitions])
        texts = {s.text: s for s in p.segments}
        self.assertNotIn("Plain-English explanation", texts)                     # filler heading dropped
        self.assertTrue(texts["Automatic coupling to simulators and data sources"].is_term)
        intro = texts["Traditionally, engineers manually integrate"].idx
        self.assertEqual(texts["databases"].parent, intro)
        self.assertNotEqual(texts["This helps people at risk during a heatwave."].kind, "risk")

    def test_example_of_is_relation_not_definition(self):
        p = tp.parse("DropEdge is an example of data augmentation.")
        self.assertEqual(p.definitions, [])
        rels = [(r.left, r.type, r.right) for s in p.segments for r in s.relations]
        self.assertIn(("DropEdge", "is an example of", "data augmentation"), rels)

    def test_targeted_prefix(self):
        p = tp.parse("Answer to Q-003: use a held-out split")
        self.assertEqual(p.segments[0].kind, "solution")
        self.assertIn("Q-003", p.segments[0].code_refs)

    def test_norm(self):
        self.assertEqual(tp.norm("The Graph-Neural Networks"), "graph neural network")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        with self.db.tx() as c:
            self.pid = E.create_project(c, "Thesis", "thesis")["id"]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def codes(self, res, key="added"):
        return {n["title"]: n["code"] for n in res[key]}

    def test_capture_and_followup(self):
        r1 = E.capture(self.db, NOTE)["results"][0]
        c1 = self.codes(r1)
        self.assertEqual(c1["Graph Neural Networks"], "C-001")
        r2 = E.capture(self.db, "@thesis\nAnswer to Q-002: DropEdge is an example of data augmentation.\n"
                                "DropEdge does not improve accuracy on PubMed.")["results"][0]
        links = {(e["src_code"], e["type"], e["dst_code"]) for e in r2["edges"]}
        sol = next(n["code"] for n in r2["added"] if n["kind"] == "solution")
        self.assertIn((sol, "solves", "Q-002"), links)
        self.assertTrue(any("contradicts" in c for c in r2["conflicts"]))
        with self.db.read() as c:
            q = E.quests(c, self.pid)
        types = {x["type"] for x in q["quests"]}
        self.assertIn("contradiction", types)
        self.assertIn("verify", types)

    def test_source_that_is_also_a_mention(self):
        # the statement first 'relates to' DropEdge, then 'derived from' it (the source), which replaces that edge
        E.capture(self.db, NOTE)
        r = E.capture(self.db, "@thesis Idea: DropEdge could reduce over-smoothing", source="DropEdge")["results"][0]
        links = {(e["src_code"], e["type"], e["dst_title"]) for e in r["edges"]}
        idea = next(n["code"] for n in r["added"] if n["kind"] == "idea")
        self.assertIn((idea, "derived from", "DropEdge"), links)
        self.assertNotIn((idea, "relates to", "DropEdge"), links)

    def test_linked_concepts_are_reused(self):
        long = "Async command pattern: API, queue, worker, state store, polling"     # 9 words: not an alias
        with self.db.tx() as c:
            E.apply_ops(c, self.pid, [{"op": "add_node", "kind": "concept", "title": "Graph Neural Networks", "aliases": ["GNNs"]},
                                      {"op": "add_node", "kind": "method", "title": long}])
            names = {x["title"]: x for x in E.concept_list(c, self.pid)}
            self.assertEqual(names["Graph Neural Networks"]["aliases"], ["GNNs"])
        for text, code in ((f"@thesis maybe [[{long}]] fits the ANX side", "M-001"),
                           ("@thesis we could reuse [[GNNs]] here", "C-001"),
                           ("@thesis [[graph neural networks]] struggle with long-range dependencies.", "C-001")):
            r = E.capture(self.db, text)["results"][0]
            self.assertIn(code, [n["code"] for n in r["updated"]], text)
            self.assertNotIn(long, [n["title"] for n in r["added"]])
            self.assertFalse(any("[[" in n["title"] + n["body"] for n in r["added"]))   # brackets stay in the note only
        with self.db.read() as c:
            self.assertIn("[[GNNs]]", c.execute("SELECT text FROM notes WHERE code='N-002'").fetchone()[0])
            self.assertEqual(E.concept_list(c, self.pid)[0]["notes"], 2)

    def test_capture_issues_carry_what_is_needed_to_act(self):
        E.capture(self.db, NOTE + "#gnn")
        issues = lambda text: {i["type"]: i for i in E.capture(self.db, text)["results"][0]["issues"]}
        it = issues("@thesis Over-smoothing is when features collapse to one constant vector.")["definition"]
        self.assertEqual((it["node_title"], it["definition"]), ("Over-smoothing", "when features collapse to one constant vector."))
        it = issues("@thesis Generic Node Nets (GNNs) are a different idea.")["alias"]
        self.assertEqual((it["alias"], it["other_title"]), ("GNNs", "Graph Neural Networks"))
        it = issues("@thesis DropEdge does not improve accuracy on PubMed. #gnn")["conflict"]
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT type FROM edges WHERE id=?", (it["edge"],)).fetchone()[0], "contradicts")
        r = E.capture(self.db, "@thesis Why does attention help on heterophilous graphs?")["results"][0]
        it = next(i for i in r["issues"] if i["type"] == "duplicate")
        with self.db.tx() as c:                                     # "No, keep it separate"
            card = E.split_from(c, it["node"], it["note"])
            self.assertNotEqual(card["node"]["id"], it["node"])
            self.assertEqual([m["note_id"] for m in card["mentions"]], [it["note"]])
            self.assertNotIn(it["note"], [m["note_id"] for m in E.node_card(c, it["node"])["mentions"]])
            t = E.tag_list(c, self.pid)[0]
            self.assertEqual((t["tag"], t["notes"]), ("gnn", 2))
        E.capture(self.db, "@thesis Pipeline latency grows with index size and query volume.")
        it = issues("@thesis Memory use climbs with batch size and index size.")["promoted"]
        self.assertEqual(it["node_title"].lower(), "index size")

    def test_source_path_becomes_openable_reference(self):
        r = E.capture(self.db, "@thesis attention helps on heterophilous graphs",
                      source='"C:\\My Papers\\zhu 2020.pdf"')["results"][0]
        ref = next(n for n in r["added"] if n["kind"] == "reference")
        self.assertEqual(ref["title"], "zhu 2020.pdf")
        self.assertEqual(ref["body"], "C:\\My Papers\\zhu 2020.pdf")
        self.assertTrue(any(e["type"] == "derived from" and e["dst"] == ref["id"] for e in r["edges"]))
        r = E.capture(self.db, '@thesis see "D:\\notes\\draft v2.docx" for the outline')["results"][0]
        self.assertIn("draft v2.docx", [n["title"] for n in r["added"]])

    def test_search_typos_prefix_and_related_terms(self):
        E.capture(self.db, NOTE)
        E.capture(self.db, "@thesis caching embeddings speeds up the retrieval pipeline.\n"
                           "the retrieval pipeline uses caching for every document.\n"
                           "reranking is slow even with caching.")
        with self.db.read() as c:
            r = S.search(c, self.pid, "oversmoothing")                # typo / spelling variant
            self.assertTrue(r["results"])
            r = S.search(c, self.pid, "Over-smoothing")
            self.assertEqual(r["results"][0]["node"]["title"], "Over-smoothing")
            self.assertTrue(any(s["m"] == 1 for s in r["results"][0]["snippet"]))
            r = S.search(c, self.pid, "dropedg")                      # still typing
            self.assertEqual(r["results"][0]["node"]["title"], "DropEdge")
            r = S.search(c, self.pid, "pipeline")                     # co-occurrence expansion
            self.assertIn("caching", r["expanded"])
            r = S.search(c, self.pid, "Q-1")
            self.assertEqual(r["results"][0]["node"]["code"], "Q-001")

    def test_search_finds_words_in_summaries_glossary_and_notes(self):
        E.capture(self.db, NOTE)
        E.capture(self.db, "@thesis met Priya at the workshop; she mentioned zeppelin datasets for later.")
        with self.db.tx() as c:
            E.apply_ops(c, self.pid, [
                {"op": "set_summary", "topic": "DropEdge", "text": "A regularizer first described by Rong et al. at Tencent."},
                {"op": "set_glossary", "entries": [{"abbr": "GNN", "expansion": "graph neural network",
                                                    "context": "popularised by Kipf at Amsterdam", "kind": "standard"},
                                                   {"abbr": "VHT", "expansion": "Virtual Human Twin",
                                                    "context": "a Brussels initiative", "kind": "text"}]}])
        with self.db.read() as c:
            r = S.search(c, self.pid, "Tencent")                     # only in Claude's summary of DropEdge
            self.assertEqual(r["results"][0]["node"]["title"], "DropEdge")
            self.assertIn("summary", r["results"][0]["fields"])
            r = S.search(c, self.pid, "Amsterdam")                   # only in a glossary entry of an existing item
            self.assertTrue(r["results"])
            r = S.search(c, self.pid, "Brussels")                    # glossary entry with no item
            self.assertEqual([t["type"] for t in r["texts"]], ["glossary"])
            r = S.search(c, self.pid, "zeppelin")                    # only in a note's full text
            self.assertEqual(r["texts"][0]["type"], "note")
            self.assertTrue(any(s["m"] == 1 and s["t"] == "zeppelin" for s in r["texts"][0]["snippet"]))

    def test_dry_run_does_not_write(self):
        E.capture(self.db, NOTE, dry_run=True)
        with self.db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], 0)

    def test_unknown_handle(self):
        r = E.capture(self.db, "@newthing an idea")
        self.assertEqual(r, {"unknown": ["newthing"]})
        r = E.capture(self.db, "@newthing Idea: try it", create_missing=True)
        self.assertEqual(r["results"][0]["project"]["handle"], "newthing")

    def test_alias_reuse_and_dedupe(self):
        E.capture(self.db, NOTE)
        r = E.capture(self.db, "@thesis GNNs struggle with long-range dependencies.")["results"][0]
        self.assertIn("C-001", [n["code"] for n in r["updated"]])
        r = E.capture(self.db, "@thesis Why does attention help on heterophilous graphs?")["results"][0]
        self.assertTrue(r["merged"])

    def test_delete_note_cleans_up(self):
        E.capture(self.db, NOTE)
        r = E.capture(self.db, "@thesis Idea: Zebra Stripes help camouflage")["results"][0]
        with self.db.tx() as c:
            d = E.delete_note(c, r["note"]["id"])
        self.assertTrue(d["removed_nodes"])
        with self.db.read() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM nodes WHERE title='Zebra Stripes'").fetchone())

    def test_search_merge_ops_and_exports(self):
        E.capture(self.db, NOTE)
        with self.db.read() as c:
            s = S.search(c, self.pid, "gnn")
            self.assertEqual(s["results"][0]["node"]["code"], "C-001")
        with self.db.tx() as c:
            log = E.apply_ops(c, self.pid, [
                {"op": "add_node", "kind": "concept", "title": "Graph rewiring", "definition": "changing edges to help flow", "ref": "gr"},
                {"op": "add_edge", "src": "$gr", "dst": "C-002", "type": "solves"},
                {"op": "mark_reviewed", "notes": "all"},
            ])
            self.assertEqual(len(log), 3)
            card = E.merge_nodes(c, E.node_by_code(c, self.pid, "C-008")["id"], E.node_by_code(c, self.pid, "C-006")["id"])
            self.assertIn("contrastive pretraining", card["aliases"])
        with self.db.read() as c:
            for fmt, (fn, _, _) in X.EXPORTERS.items():
                self.assertTrue(len(fn(c, self.pid)) > 100, fmt)
            data = json.loads(X.to_json(c, self.pid))
        with self.db.tx() as c:
            p2 = X.import_project(c, data)
            self.assertEqual(p2["handle"], "thesis-2")
            self.assertIsNotNone(E.node_by_code(c, p2["id"], "C-001"))


class BriefTests(unittest.TestCase):
    setUp, tearDown = EngineTests.setUp, EngineTests.tearDown
    SECTION = ("@thesis Plain-English explanation\n\n"
               "2. “DropEdge for deep GNNs”\n"
               "It randomly removes edges during training.\n\n"
               "That slows down over-smoothing, so deeper models stay useful.\n\n"
               "3. “Something else”\nunrelated text about budgets.")

    def brief(self, q):
        with self.db.read() as c:
            return B.brief(c, self.pid, q)

    def test_gathers_items_definitions_and_passages(self):
        E.capture(self.db, NOTE)
        E.capture(self.db, self.SECTION)
        b = self.brief("DropEdge")
        self.assertEqual(b["topic"]["title"], "DropEdge")
        items = {i["title"]: i for g in b["groups"] for i in g["items"]}
        self.assertTrue(any("data augmentation" in t for t in items))          # the question about it
        self.assertTrue(any("4.2% accuracy" in (i["body"] or "") for i in items.values()))
        self.assertEqual(b["stats"]["notes"], 2)
        first, second = b["timeline"]
        self.assertLess(first["note"]["created"], second["note"]["created"] + 1e-9)
        self.assertTrue(any(s["m"] and s["t"] == "DropEdge" for p in first["passages"] for s in p))
        # a heading about the topic brings its section, and stops at the next heading
        section = ["".join(s["t"] for s in p) for p in second["passages"]]
        self.assertTrue(any("removes edges" in p and "deeper models" in p for p in section))
        self.assertFalse(any("budgets" in p for p in section))
        b = self.brief("Over-smoothing")
        self.assertIn("node embeddings become indistinguishable", b["definitions"][0]["text"])
        self.assertEqual(self.brief("oversmoothing")["topic"]["title"], "Over-smoothing")
        self.assertEqual(self.brief("dropegde")["corrected"], "DropEdge")          # typo -> spelling used in notes
        self.assertEqual(self.brief("zebra")["stats"]["notes"], 0)

    def test_glossary_terms(self):
        with self.db.tx() as c:
            log = E.apply_ops(c, self.pid, [{"op": "set_glossary", "source": "partB.pdf", "entries": [
                {"abbr": "ETN", "expansion": "Edge Twin Node", "kind": "list", "page": 1, "context": "secure on-prem node"},
                {"abbr": "AGE", "expansion": "AGE Platform Europe", "kind": "partner"},
                {"abbr": "VHT", "expansion": "Virtual Human Twin", "kind": "text", "page": 2}]}])
            self.assertEqual(log, ["glossary: 3 entries"])
        E.capture(self.db, "@thesis ETNs keep personal data at home.")
        E.capture(self.db, "@thesis Every Edge Twin Node needs a dry run before execution.")
        E.capture(self.db, "@thesis Older adults of a certain age struggle with apps; AGE can help recruit them.")
        with self.db.read() as c:
            g = {x["abbr"]: x for x in E.glossary(c, self.pid)}
        self.assertEqual((g["ETN"]["notes"], g["AGE"]["notes"], g["VHT"]["notes"]), (2, 1, 0))
        b = self.brief("etn")                                      # abbreviation, any case: both forms are found
        self.assertEqual(b["glossary"]["expansion"], "Edge Twin Node")
        self.assertEqual(b["stats"]["notes"], 2)
        self.assertTrue(any(s["m"] and s["t"] == "ETNs" for e in b["timeline"] for p in e["passages"] for s in p))
        b = self.brief("AGE")                                      # the partner, not the word "age"
        passage = "".join(s["t"] for s in b["timeline"][0]["passages"][0])
        self.assertIn("AGE can help", passage)
        self.assertEqual([s["t"] for s in b["timeline"][0]["passages"][0] if s["m"]], ["AGE"])
        with self.db.read() as c:
            r = S.search(c, self.pid, "ETN")
            self.assertEqual(r["glossary"]["expansion"], "Edge Twin Node")
            self.assertTrue(any("dry run" in (x["node"]["title"] + "".join(s["t"] for s in x["snippet"])) for x in r["results"]))
            st = B.summary_status(c, self.pid)["glossary_terms_to_summarise"]
            self.assertEqual({x["abbr"] for x in st}, {"ETN", "AGE"})
            md = B.to_markdown(B.brief(c, self.pid, "ETN"), E.get_project(c, self.pid))
            self.assertIn("**Glossary:** ETN = Edge Twin Node (the proposal's list of abbreviations, p.1)", md)
            data = json.loads(X.to_json(c, self.pid))
        with self.db.tx() as c:
            E.apply_ops(c, self.pid, [{"op": "set_summary", "topic": "ETN", "text": "The user-side tier."}])
            p2 = X.import_project(c, data)
            self.assertEqual(len(E.glossary(c, p2["id"])), 3)
        self.assertEqual(self.brief("Edge Twin Node")["summary"]["text"], "The user-side tier.")

    def test_requirements_tracking(self):
        E.capture(self.db, NOTE)
        entry = {"rid": "SO1.2", "grp": "SO1", "grp_title": "Platform", "title": "Workflow portability",
                 "text": "≥90% of workflows run unchanged", "page": 4, "wp": "WP4–WP8", "concept": "$wp"}
        with self.db.tx() as c:
            log = E.apply_ops(c, self.pid, [
                {"op": "add_node", "kind": "concept", "title": "Workflow portability", "ref": "wp"},
                {"op": "add_edge", "src": "Q-001", "dst": "$wp", "type": "relates to"},
                {"op": "set_requirements", "source": "partB.pdf", "entries": [entry, dict(entry, rid="SO7.1", grp="SO7", title="RRI strategy", concept=None, due="M24")]}])
            self.assertEqual(log[-1], "requirements: 2 entries")
            r = E.update_requirement(c, self.pid, "SO1.2", {"status": "In progress", "progress": "40", "owner": "Prem", "note": "ACP test pending"})
            self.assertEqual((r["status"], r["progress"], r["node_title"], r["notes"]), ("in progress", 40, "Workflow portability", 1))
            r = E.update_requirement(c, self.pid, "SO1.2", {"status": "done"})
            self.assertEqual(r["progress"], 100)                        # done implies 100% unless given
            self.assertEqual([x["field"] for x in E.requirement_log(c, self.pid, "SO1.2")], ["progress", "status", "note", "owner", "progress", "status"])
            with self.assertRaises(E.AtlasError):
                E.update_requirement(c, self.pid, "SO1.2", {"status": "finished"})
            # refreshing from the document keeps the user's tracking
            E.apply_ops(c, self.pid, [{"op": "set_requirements", "entries": [dict(entry, text="≥90% of workflows", concept=None)]}])
            r = next(x for x in E.requirement_list(c, self.pid) if x["rid"] == "SO1.2")
            self.assertEqual((r["text"], r["status"], r["owner"], r["node_title"]), ("≥90% of workflows", "done", "Prem", "Workflow portability"))
            self.assertEqual(next(x for x in E.requirement_list(c, self.pid) if x["rid"] == "SO7.1")["due"], "M24")
            self.assertEqual(E.list_projects(c)[0]["requirement_count"], 2)
            data = json.loads(X.to_json(c, self.pid))
            p2 = X.import_project(c, data)
            r2 = {x["rid"]: x for x in E.requirement_list(c, p2["id"])}
            self.assertEqual((r2["SO1.2"]["status"], r2["SO1.2"]["node_title"], r2["SO1.2"]["changes"]), ("done", "Workflow portability", 6))

    def test_summary_is_flagged_when_newer_notes_arrive(self):
        E.capture(self.db, NOTE)
        with self.db.tx() as c:
            code = E.node_by_code(c, self.pid, "C-001")["code"]
            log = E.apply_ops(c, self.pid, [{"op": "set_summary", "topic": code, "text": "GNNs learn over graphs."},
                                            {"op": "set_summary", "topic": "DropEdge", "text": "A regulariser."}])
            self.assertEqual(log, ["summary for 'Graph Neural Networks'", "summary for 'DropEdge'"])
        s = self.brief("GNNs")["summary"]                       # found through the alias
        self.assertEqual((s["text"], s["newer_notes"]), ("GNNs learn over graphs.", 0))
        E.capture(self.db, "@thesis DropEdge does not help on PubMed.")
        with self.db.tx() as c:                                 # written a minute after the summary
            c.execute("UPDATE notes SET created=created+60 WHERE text LIKE '%PubMed%'")
        self.assertEqual(self.brief("DropEdge")["summary"]["newer_notes"], 1)
        with self.db.read() as c:
            st = B.summary_status(c, self.pid)
            self.assertEqual({x["topic"]: x["newer_notes"] for x in st["summaries"]},
                             {"DropEdge": 1, "Graph Neural Networks": 0})
            p = E.get_project(c, self.pid)
            md = B.to_markdown(B.brief(c, self.pid, "DropEdge"), p)
            self.assertIn("## Summary (Claude's interpretation", md)
            self.assertIn("**DropEdge**", md)
            data = json.loads(X.to_json(c, self.pid))
        with self.db.tx() as c:
            p2 = X.import_project(c, data)
            self.assertIsNotNone(B.brief(c, p2["id"], "DropEdge")["summary"])
            E.apply_ops(c, self.pid, [{"op": "clear_summary", "topic": "dropedge"}])
        self.assertIsNone(self.brief("DropEdge")["summary"])


class LifecycleTests(unittest.TestCase):
    def test_hidden_window_state(self):
        from atlas import server as SV
        a = SV.App(db=None, cfg={})
        self.assertFalse(a.hidden)
        SV.ping(a, None, {"state": "hidden"}, {})
        self.assertTrue(a.hidden)
        SV.ping(a, None, {}, {})                     # a plain ping keeps the last known state
        self.assertTrue(a.hidden)
        SV.ping(a, None, {"state": "visible"}, {})
        self.assertFalse(a.hidden)

    def test_limits(self):
        from atlas import main as M
        self.assertGreater(M.HIDDEN_LIMIT, M.IDLE_LIMIT)
        self.assertGreater(M.SLEEP_JUMP, 1)


if __name__ == "__main__":
    unittest.main()
