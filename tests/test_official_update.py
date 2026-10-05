from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.request

import pytest

from ipg_pipeline import official_update as update
from ipg_pipeline.core import ROOT, dump_yaml, load_json, load_yaml, sha256_bytes, sha256_text, walk_nodes
from ipg_pipeline.full_parser import FullParseError, extract_full_pdf, parse_full_extraction
from ipg_pipeline.official_inheritance import inherit_editorial_state
from ipg_pipeline.production import load_project
from ipg_pipeline.reconcile import official_records, override_evidence, reconcile_update_document
from ipg_pipeline.snapshot import artifact_hashes, verify_snapshot
from tests.test_production import make_rehearsal, TEST_RELEASE


PDF_URL = "https://media.wizards.com/ContentResources/WPN/test-only.pdf"
PDF = b"%PDF-1.7\nTEST ONLY synthetic downloader fixture, parser explicitly injected"


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _page(date="Jan 1, 2000", url=PDF_URL):
    return f'<div class="downloadableDocument"><div class="titleText">{update.EXPECTED_TITLE}</div><div class="updated">{date}</div><a href="{url}">Download</a></div>'


def _temporary(document, digest):
    parsed = {"schemaVersion": 1, "document": copy.deepcopy(document), "pdf": {"sha256": digest, "pages": 1},
        "coverage": {"lineCount": 1, "classifiedLineCount": 1, "unclassifiedLineCount": 0,
                     "lines": [{"lineId": "test-only-extraction-1", "disposition": "official-block-text"}]}}
    remap = {}
    for index, (kind, node) in enumerate(walk_nodes([parsed["document"]]), 1):
        remap[node["id"]] = f"extract-{kind}-{index:04d}"
        node["id"] = remap[node["id"]]
        if kind == "block":
            node["text"]["zh"] = ""
            node["officialPdfUnits"][0]["pdfSha256"] = digest
        elif kind == "section":
            node["title"]["zh"] = ""
    parsed["document"]["publicationAnnotations"] = []
    return parsed


def _document(digest):
    template = load_yaml(ROOT / "tests/fixtures/release-rehearsal/source.yaml")
    section = template["sections"][0]
    section.update(id="ipg-s1-1", kind="policy", number="1.1", title={"en": "TEST ONLY Alpha policy", "zh": "合成甲政策"})
    component = section["components"][0]; component["id"] = "ipg-c000001"
    group = component["groups"][0]; group["id"] = "ipg-g000001"
    block = group["blocks"][0]
    group["blocks"] = []
    for i, name in enumerate(("alpha", "beta", "gamma"), 1):
        b = copy.deepcopy(block); b.update(id=f"ipg-b{i:06d}", text={"en": "TEST ONLY " + name, "zh": "合成译文 " + name})
        b["officialPdfUnits"][0]["pdfSha256"] = digest
        group["blocks"].append(b)
    second = copy.deepcopy(section); second.update(id="ipg-s1-2", number="1.2", title={"en": "TEST ONLY Delta policy", "zh": "合成乙政策"})
    second["components"][0]["id"] = "ipg-c000002"
    second["components"][0]["groups"][0]["id"] = "ipg-g000002"
    second["components"][0]["groups"][0]["blocks"] = [copy.deepcopy(group["blocks"][0])]
    second["components"][0]["groups"][0]["blocks"][0].update(id="ipg-b000004", text={"en": "TEST ONLY delta", "zh": "合成译文 delta"})
    template["sections"].append(second)
    template["publicationAnnotations"] = [{"id": "ipg-ann-test", "anchor": {"type": "block", "id": "ipg-b000001"}, "position": "after", "order": 10,
        "groups": [{"id": "ipg-ann-test-g01", "kind": "paragraphs", "blocks": [{"id": "ipg-ann-test-b01", "type": "paragraph", "text": {"en": "TEST ONLY publication note", "zh": "合成发布注解"}}]}]}]
    return template


def _registry(document):
    entries = [{"id": r["node"]["id"], "kind": r["kind"], "status": "active", "canonicalKey": "test:" + r["node"]["id"], "history": []} for r in official_records([document])]
    for a in document["publicationAnnotations"]:
        entries.extend({"id": n["id"], "kind": "annotation", "status": "active", "canonicalKey": "test:" + n["id"], "history": []} for n in [a, *a["groups"], *(b for g in a["groups"] for b in g["blocks"])])
    return {"entries": entries}


@pytest.fixture
def repo(tmp_path):
    root = make_rehearsal(tmp_path / "TEST-ONLY")
    digest = sha256_bytes(PDF)
    release = root / "src/ipg/releases" / TEST_RELEASE
    manifest = load_yaml(release / "manifest.yaml")
    manifest["versions"]["official"].update(pdfSha256=digest, url=PDF_URL)
    (release / "manifest.yaml").write_text(dump_yaml(manifest), encoding="utf-8")
    doc = load_yaml(release / "test-only.yaml"); doc["sections"][0]["components"][0]["groups"][0]["blocks"][0]["officialPdfUnits"][0]["pdfSha256"] = digest
    (release / "test-only.yaml").write_text(dump_yaml(doc), encoding="utf-8")
    pdf = root / f"snapshots/official/2000-01-01/{digest}/test-only.pdf"
    pdf.parent.mkdir(parents=True); pdf.write_bytes(PDF)
    summary = load_json(root / "tests/synthetic-migration-summary.json"); summary["authority"]["officialPdfSha256"] = digest
    _write(root / "tests/synthetic-migration-summary.json", summary)
    migration_path = root / f"review/migration/{TEST_RELEASE}.json"
    migration = load_json(migration_path); migration["authority"]["officialPdfSha256"] = digest
    migration["evidence"]["sha256"] = sha256_bytes((root / "tests/synthetic-migration-summary.json").read_bytes())
    _write(migration_path, migration)
    (root / "src/ipg/mapping-overrides.yaml").write_text("schemaVersion: 1\n", encoding="utf-8")
    for name in ("outputs/omegat-ipg-full/source/user.po", "outputs/current-candidate/IPG.md", "dist/IPG.md", "dist/rules.json"):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"USER OWNED TEST SENTINEL")
    return root


def _protected(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_relative_to(root / "outputs/official-update")}


def _check(root, *, date="2000-01-01", payload=PDF, mutate=None, timestamp="2001-01-01T00:00:00Z", override_path=None):
    project = load_project(root)
    def parser(path, **kwargs):
        assert path.read_bytes() == payload and kwargs["expected_sha256"] == sha256_bytes(payload)
        parsed = _temporary(project["documents"][0][1], sha256_bytes(payload))
        if mutate:
            mutate(parsed)
        return parsed
    return update.check_official_update(root, override_path=override_path,
        discover_release=lambda: ({"pageUrl": update.RULES_PAGE, "title": update.EXPECTED_TITLE, "effectiveDate": date, "url": PDF_URL, "pageRedirects": []}, _page().encode()),
        downloader=lambda url, kind: (payload, url, []), parser=parser, downloaded_at=timestamp, date_reader=lambda pdf: date)


def test_exact_discovery_ignores_other_documents_and_filename_date():
    page = _page("Sep 23, 2024", "https://media.wizards.com/2025/not-a-date.pdf") + _page().replace(update.EXPECTED_TITLE, "Magic Tournament Rules")
    result = update.parse_rules_page(page)
    assert result["pageUpdatedDate"] == "2024-09-23" and result["url"].endswith("not-a-date.pdf")


@pytest.mark.parametrize("html", ["<html>no entries</html>", _page() * 2, _page("Tomorrow"), _page("Feb 30, 2024"), _page().replace("titleText", "unknown"), _page()[:-6], _page().replace('</a>', '</a><a href="https://media.wizards.com/other.pdf">Other</a>')])
def test_page_anomalies_fail_closed(html):
    with pytest.raises(ValueError): update.parse_rules_page(html)


@pytest.mark.parametrize("url,kind", [("http://wpn.wizards.com/en/rules-documents", "page"), ("https://evil.example/en/rules-documents", "page"), ("https://wpn.wizards.com/elsewhere", "page"), ("https://media.wizards.com.evil.example/a.pdf", "pdf"), ("https://u:p@media.wizards.com/a.pdf", "pdf"), ("https://media.wizards.com:8443/a.pdf", "pdf"), ("https://media.wizards.com/a.exe", "pdf"), ("https://media.wizards.com/a.pdf?token=x", "pdf"), ("https://media.wizards.com/%2e%2e/a.pdf", "pdf"), ("https://media.wizards.com/a%0a.pdf", "pdf")])
def test_url_boundaries(url, kind):
    with pytest.raises(ValueError): update.validate_url(url, kind)


def test_redirect_is_checked_before_network_follow(monkeypatch):
    handler = update.SafeRedirect("pdf")
    called = []
    monkeypatch.setattr(urllib.request.HTTPRedirectHandler, "redirect_request", lambda *a: called.append(a))
    with pytest.raises(ValueError): handler.redirect_request(None, None, 302, "", {}, "https://evil.example/a.pdf")
    assert not called
    for i in range(5): handler.redirect_request(None, None, 302, "", {}, PDF_URL)
    with pytest.raises(ValueError): handler.redirect_request(None, None, 302, "", {}, PDF_URL)
    assert len(called) == 5


@pytest.mark.parametrize("fault", ["length", "stream", "header", "redirect", "encoding", "truncated"])
def test_download_limits_and_pdf_signature(monkeypatch, fault):
    body = PDF
    headers = {}
    final = PDF_URL
    monkeypatch.setattr(update, "MAX_PDF_BYTES", 100)
    if fault == "length": headers["Content-Length"] = "101"
    if fault == "stream": body = b"%PDF-" + b"x" * 101
    if fault == "header": body = b"not PDF"
    if fault == "redirect": final = "https://evil.example/a.pdf"
    if fault == "encoding": headers["Content-Encoding"] = "gzip"
    if fault == "truncated": headers["Content-Length"] = str(len(body) + 1)
    class Response(io.BytesIO):
        def geturl(self): return final
    response = Response(body); response.headers = headers
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **k: response))
    with pytest.raises(ValueError): update.download(PDF_URL, "pdf")


@pytest.mark.parametrize("case", ["unchanged", "new-date", "same-day"])
def test_update_isolated_immutable_deterministic_and_three_axis(repo, case):
    before = _protected(repo)
    payload = PDF if case == "unchanged" else PDF + b" NEW VERSION"
    date = "2000-02-01" if case == "new-date" else "2000-01-01"
    first = _check(repo, date=date, payload=payload)
    snapshot = repo / first["snapshot"]
    hashes = artifact_hashes(snapshot)
    second = _check(repo, date=date, payload=payload)
    assert first == second and hashes == artifact_hashes(snapshot)
    assert verify_snapshot(snapshot)["official"]["pdfSha256"] == sha256_bytes(payload)
    assert first["diff"]["summary"]["changed"] == 0
    assert first["inheritance"]["officialIdsPreserved"] == 4
    assert before == _protected(repo)
    if case == "unchanged":
        assert first["status"] == "no-update" and not (snapshot / "candidate").exists()
    else:
        assert first["changed"] and first["requiresApproval"]
        manifest = load_yaml(snapshot / "candidate/source/manifest.yaml")
        assert not manifest["publishable"] and manifest["versions"]["official"]["effectiveDate"] == date
        assert manifest["versions"]["annotations"] == load_project(repo)["manifest"]["versions"]["annotations"]
        assert manifest["versions"]["translation"]["revision"].startswith("zh-test-update-")
        assert "不得发布" in (snapshot / "candidate/preview/IPG.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("fault", ["rollback", "invalid-pdf", "parse", "source-race"])
def test_failure_preserves_current_stores_and_no_snapshot(repo, fault):
    before = _protected(repo)
    if fault == "rollback":
        with pytest.raises(ValueError, match="回退"): _check(repo, date="1999-01-01")
    elif fault == "invalid-pdf":
        with pytest.raises(ValueError, match="非法"): _check(repo, payload=b"not PDF")
    elif fault == "parse":
        with pytest.raises(FullParseError): _check(repo, payload=PDF + b"NEW", mutate=lambda parsed: (_ for _ in ()).throw(FullParseError([{"code": "unclassified-body"}])))
    else:
        def mutate(parsed):
            # Simulate an external change; the checker must detect it, not undo it.
            (repo / "src/ipg/version-notes.md").write_text("# 版本说明\n\nexternal edit\n", encoding="utf-8")
        with pytest.raises(ValueError, match="输入文件发生变化"): _check(repo, payload=PDF + b"NEW", mutate=mutate)
        before.pop("src/ipg/version-notes.md")
    after = _protected(repo)
    if fault == "source-race": after.pop("src/ipg/version-notes.md")
    assert before == after and not list((repo / "outputs/official-update").rglob("snapshot.json"))


@pytest.mark.parametrize("fault", ["modified", "missing", "extra", "manifest", "hashlist"])
def test_snapshot_tampering_is_detected_without_overwrite(repo, fault):
    report = _check(repo)
    path = repo / report["snapshot"]
    if fault == "modified": (path / "official/IPG_EN.pdf").write_bytes(PDF + b"bad")
    if fault == "missing": (path / "parsed/official.json").unlink()
    if fault == "extra": (path / "extra.txt").write_text("bad")
    if fault == "manifest": (path / "snapshot.json").write_text("{}")
    if fault == "hashlist": (path / "SHA256SUMS").write_text("bad")
    damaged = {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, KeyError)): verify_snapshot(path)
    with pytest.raises((ValueError, KeyError)): _check(repo)
    assert damaged == {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def model():
    doc = _document(sha256_bytes(PDF))
    return doc, _temporary(doc, sha256_bytes(PDF + b"new")), _registry(doc)


def _blocks(document, section=0): return document["sections"][section]["components"][0]["groups"][0]["blocks"]


@pytest.mark.parametrize("change", ["insert", "delete", "reorder", "rename", "renumber", "move"])
def test_native_identity_changes_are_not_array_positions(model, change):
    old, new, registry = model
    nodes = _blocks(new["document"])
    if change == "insert":
        extra = copy.deepcopy(nodes[0]); extra.update(id="extract-block-new", text={"en": "TEST ONLY new", "zh": ""}); nodes.insert(1, extra)
    if change == "delete": nodes.pop(1)
    if change == "reorder": nodes.reverse()
    if change == "rename": new["document"]["sections"][0]["title"]["en"] = "TEST ONLY renamed Alpha"
    if change == "renumber": new["document"]["sections"][0]["number"] = "1.3"
    if change == "move": _blocks(new["document"], 1).append(nodes.pop(1))
    before = copy.deepcopy(registry)
    result = reconcile_update_document(new, [old], registry, version="test-v2")
    ids = {b["text"]["en"]: b["id"] for _, b in walk_nodes([result["document"]]) if "text" in b}
    assert ids["TEST ONLY alpha"] == "ipg-b000001"
    assert ids["TEST ONLY gamma"] == "ipg-b000003"
    assert result["reconciliation"]["findings"] == [] and registry == before
    if change == "insert": assert ids["TEST ONLY new"] == "ipg-b000005"
    if change == "delete": assert next(e for e in result["registry"]["entries"] if e["id"] == "ipg-b000002")["status"] == "retired"
    else: assert ids["TEST ONLY beta"] == "ipg-b000002"
    if change in {"rename", "renumber"}: assert result["document"]["sections"][0]["id"] == "ipg-s1-1"


def _operation(old, parsed, sources, targets, operation="continue", preserve=None):
    old_nodes = {r["node"]["id"]: r["node"] for r in official_records([old])}
    new_nodes = {r["node"]["id"]: r["node"] for r in official_records([parsed["document"]])}
    item = {"operation": operation, "kind": "block", "fromIds": sources, "toExtractionIds": targets,
        "sourceHashes": {i: override_evidence(old_nodes[i]) for i in sources}, "targetHashes": {i: override_evidence(new_nodes[i]) for i in targets}, "reason": "TEST ONLY explicit identity decision"}
    if preserve: item.update(preserveId=preserve, preserveExtractionId=targets[0])
    return {"mappings": [item]}


@pytest.mark.parametrize("operation", ["split", "merge"])
def test_split_merge_override_and_retirement_are_explicit(model, operation):
    old, new, registry = model; blocks = _blocks(new["document"])
    if operation == "split":
        first = copy.deepcopy(blocks[1]); first["text"]["en"] = "TEST ONLY beta part 1"
        second = copy.deepcopy(first); second.update(id="extract-block-split", text={"en": "TEST ONLY beta part 2", "zh": ""})
        blocks[1:2] = [first, second]
        override = _operation(old, new, ["ipg-b000002"], [first["id"], second["id"]], operation)
    else:
        blocks[1]["text"]["en"] = "TEST ONLY beta gamma combined"
        override = _operation(old, new, ["ipg-b000002", "ipg-b000003"], [blocks[1]["id"]], operation)
        blocks.pop(2)
    result = reconcile_update_document(new, [old], registry, version="test-v2", overrides=override)
    assert result["reconciliation"]["findings"] == []
    assert "ipg-b000002" in result["reconciliation"]["retiredIds"]
    assert all(b["id"] not in {"ipg-b000002", "ipg-b000003"} for b in _blocks(result["document"])[1:] if "part" in b["text"]["en"] or "combined" in b["text"]["en"])


def test_ambiguous_evidence_is_not_assigned_or_silently_retired(model):
    old, new, registry = model
    _blocks(old)[1]["text"]["en"] = _blocks(old)[0]["text"]["en"]
    _blocks(new["document"])[1]["text"]["en"] = _blocks(new["document"])[0]["text"]["en"]
    result = reconcile_update_document(new, [old], registry, version="test-v2")
    assert any(f["code"] == "ambiguous-stable-identity" for f in result["reconciliation"]["findings"])
    assert _blocks(result["document"])[0]["id"] not in {"ipg-b000001", "ipg-b000002"}
    assert not {"ipg-b000001", "ipg-b000002"}.intersection(result["reconciliation"]["retiredIds"])


@pytest.mark.parametrize("fault", ["old-hash", "new-hash", "reuse", "retired"])
def test_invalid_override_fails_closed(model, fault):
    old, new, registry = model
    target = _blocks(new["document"])[0]["id"]
    override = _operation(old, new, ["ipg-b000001"], [target])
    if fault == "old-hash": override["mappings"][0]["sourceHashes"]["ipg-b000001"] = "0" * 64
    if fault == "new-hash": override["mappings"][0]["targetHashes"][target] = "0" * 64
    if fault == "reuse": override["mappings"] *= 2
    if fault == "retired": next(e for e in registry["entries"] if e["id"] == "ipg-b000001")["status"] = "retired"
    with pytest.raises(ValueError): reconcile_update_document(new, [old], registry, version="test-v2", overrides=override)


def test_inheritance_translation_review_notes_and_lost_annotation(model):
    from ipg_pipeline.omegat import collect_units
    from ipg_pipeline.review import build_review_ledger
    old, new, registry = model
    blocks = _blocks(new["document"])
    blocks[1]["text"]["en"] = "TEST ONLY beta revised"
    override = _operation(old, new, ["ipg-b000002"], [blocks[1]["id"]])
    display = {"schemaVersion": 1, "values": {"component.body": {"en": "Body", "zh": "测试正文"}}}
    units = collect_units([("test.yaml", old)], display, release_relative="test")
    actions = [{"unitId": u["id"], "sourceHash": sha256_text(u["source"]), "targetHash": sha256_text(u["target"]), "reviewedAt": "2000-01-01"} for u in units]
    ledger = build_review_ledger(units, actions, release_id="ipg-old", translation_revision="zh-old")
    note = {"unitId": "block:ipg-b000002", "sourceHash": sha256_text("TEST ONLY beta"), "targetHash": sha256_text("合成译文 beta"), "releaseId": "ipg-old", "translationRevision": "zh-old", "note": "TEST ONLY rationale", "recordedAt": "2000-01-01", "stale": False}
    project = {"documents": [("test.yaml", old)], "display": display, "ledger": ledger}
    manifest = {"releaseId": "ipg-new", "versions": {"translation": {"revision": "zh-new"}}, "scope": {"publicationAnnotations": {}}}
    result = reconcile_update_document(new, [old], registry, version="test-v2", overrides=override)
    inherited = inherit_editorial_state(project, result, copy.deepcopy(manifest), {"schemaVersion": 1, "notes": [note]})
    assert _blocks(result["document"])[1]["text"]["zh"] == "合成译文 beta"
    states = {e["unitId"]: e["status"] for e in inherited["ledger"]["entries"]}
    assert states["block:ipg-b000002"] == "stale" and states["block:ipg-b000001"] == "reviewed-unchanged"
    assert inherited["notes"]["notes"][0]["stale"]
    assert inherited["inheritance"]["annotationsInherited"] == 1
    assert "TEST ONLY rationale" not in json.dumps(inherited["documents"])
    deleted = copy.deepcopy(new); _blocks(deleted["document"]).pop(0)
    lost = inherit_editorial_state(project, reconcile_update_document(deleted, [old], registry, version="test-v3", overrides=override), copy.deepcopy(manifest))
    assert lost["inheritance"]["annotationsUnresolved"] == 1
    assert lost["unresolvedAnnotations"][0]["annotation"] == old["publicationAnnotations"][0]
    assert not lost["ledger"]["entries"] or all(e["unitId"] != "block:ipg-b000001" for e in lost["ledger"]["entries"])


def test_deleted_id_is_never_reused_on_next_version(model):
    old, new, registry = model; _blocks(new["document"]).pop(1)
    deleted = reconcile_update_document(new, [old], registry, version="v2")
    again = _temporary(deleted["document"], sha256_bytes(PDF + b"v3"))
    node = copy.deepcopy(_blocks(again["document"])[0]); node.update(id="extract-new", text={"en": "TEST ONLY beta", "zh": ""}); _blocks(again["document"]).append(node)
    result = reconcile_update_document(again, [deleted["document"]], deleted["registry"], version="v3")
    assert _blocks(result["document"])[-1]["id"] != "ipg-b000002"


def test_update_parser_uses_new_date_hash_and_dynamic_appendix_pages():
    from ipg_pipeline.p3 import OFFICIAL_PDF
    extraction = extract_full_pdf(OFFICIAL_PDF)
    digest = sha256_bytes(PDF + b"2040")
    extraction["pdf"].update(sha256=digest, pages=33)
    for line in extraction["lines"]:
        if line["text"].startswith("Effective "): line["text"] = "Effective January 1, 2040"
        if line["page"] in (30, 31):
            old_page = line["page"]; line["page"] += 2
            if line["text"] == str(old_page): line["text"] = str(line["page"])
        if line["text"].startswith("Appendix A") and "..." in line["text"]: line["text"] = line["text"].rsplit(" ", 1)[0] + " 32"
        if line["text"].startswith("Appendix B") and "..." in line["text"]: line["text"] = line["text"].rsplit(" ", 1)[0] + " 33"
    parsed = parse_full_extraction(extraction, expected_sha256=digest, expected_date="2040-01-01", frozen=False)
    assert parsed["coverage"]["classifiedLineCount"] == 1050
    assert all(p["pdfSha256"] == digest for k,b in walk_nodes([parsed["document"]]) if k == "block" for p in b["officialPdfUnits"])
    with pytest.raises(FullParseError): parse_full_extraction(extraction, expected_sha256=digest, expected_date="2040-02-01", frozen=False)


def test_same_day_packages_and_different_bases_get_separate_snapshots(repo):
    first = _check(repo, payload=PDF + b"package A")
    a = repo / first["snapshot"]; hashes = artifact_hashes(a)
    second = _check(repo, payload=PDF + b"package B")
    assert first["snapshot"] != second["snapshot"] and artifact_hashes(a) == hashes
    notes = repo / "src/ipg/version-notes.md"
    notes.write_text("# 版本说明\n\nTEST ONLY next local revision notes\n", encoding="utf-8")
    third = _check(repo, payload=PDF + b"package A")
    assert first["snapshot"] != third["snapshot"] and artifact_hashes(a) == hashes


def test_reuse_retains_first_download_time_and_bytes(repo):
    first = _check(repo, timestamp="2001-01-01T00:00:00Z")
    second = _check(repo, timestamp="2002-01-01T00:00:00Z")
    assert first == second
    assert verify_snapshot(repo / first["snapshot"])["official"]["downloadedAt"] == "2001-01-01T00:00:00Z"


def test_new_unit_remains_untranslated_and_unreviewed(repo):
    def mutate(parsed):
        blocks = _blocks(parsed["document"])
        extra = copy.deepcopy(blocks[0]); extra.update(id="extract-block-new", text={"en": "TEST ONLY added paragraph", "zh": ""}); blocks.append(extra)
    report = _check(repo, payload=PDF + b"NEW", mutate=mutate)
    path = repo / report["snapshot"]
    doc = load_yaml(path / "candidate/source/front-matter.yaml")
    added = _blocks(doc)[-1]
    assert added["text"]["zh"] == ""
    revision = load_yaml(path / "candidate/source/manifest.yaml")["versions"]["translation"]["revision"]
    ledger = load_json(path / f"candidate/review/status/{revision}.json")
    assert next(e for e in ledger["entries"] if e["unitId"] == "block:" + added["id"])["status"] == "unreviewed"


def test_modified_without_override_keeps_suggestion_not_guessed_identity(repo):
    def mutate(parsed): _blocks(parsed["document"])[0]["text"]["en"] = "TEST ONLY changed authoritative English"
    report = _check(repo, payload=PDF + b"NEW", mutate=mutate)
    finding = next(f for f in report["findings"] if f["code"] == "modified-identity-needs-override")
    assert finding["oldTranslations"][0]["text"]["zh"] == "合成契约测试夹具。"
    assert finding["provisionalId"] != "ipg-test-block"


@pytest.mark.parametrize("anchor_kind", ["section", "component", "group", "block"])
def test_publication_annotations_keep_native_anchor_position_and_order(model, anchor_kind):
    old, new, registry = model
    node = next(r["node"] for r in official_records([old]) if r["kind"] == anchor_kind)
    old["publicationAnnotations"][0]["anchor"] = {"type": anchor_kind, "id": node["id"]}
    result = reconcile_update_document(new, [old], registry, version="test-v2")
    project = {"documents": [("test.yaml", old)], "display": {"values": {}}, "ledger": None}
    manifest = {"releaseId": "ipg-new", "versions": {"translation": {"revision": "zh-new"}}, "scope": {"publicationAnnotations": {}}}
    inherited = inherit_editorial_state(project, result, manifest)
    annotation = next(a for _,d in inherited["documents"] for a in d["publicationAnnotations"])
    assert annotation == old["publicationAnnotations"][0]


def test_split_with_explicit_continuity_keeps_only_selected_id_and_old_target(model):
    old, new, registry = model; nodes = _blocks(new["document"])
    first = copy.deepcopy(nodes[1]); first["text"]["en"] = "TEST ONLY first split"
    second = copy.deepcopy(first); second.update(id="extract-second", text={"en": "TEST ONLY second split", "zh": ""})
    nodes[1:2] = [first, second]
    override = _operation(old, new, ["ipg-b000002"], [first["id"], second["id"]], "split", preserve="ipg-b000002")
    result = reconcile_update_document(new, [old], registry, version="v2", overrides=override)
    assert _blocks(result["document"])[1]["id"] == "ipg-b000002"
    assert _blocks(result["document"])[1]["text"]["zh"] == "合成译文 beta"
    assert _blocks(result["document"])[2]["text"]["zh"] == ""


@pytest.mark.parametrize("store", ["ledger", "notes", "override"])
def test_malformed_editorial_input_is_not_silently_discarded(repo, store):
    path = repo / ("review/status/zh-test.json" if store == "ledger" else "review/translation-notes/zh-test.json" if store == "notes" else "bad-overrides.json")
    _write(path, {})
    before = _protected(repo)
    with pytest.raises(ValueError): _check(repo, override_path=path if store == "override" else None)
    assert _protected(repo) == before


def test_html_download_is_size_bounded(monkeypatch):
    monkeypatch.setattr(update, "MAX_PAGE_BYTES", 10)
    class Response(io.BytesIO):
        headers = {}
        def geturl(self): return update.RULES_PAGE
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **k: Response(b"x" * 11)))
    with pytest.raises(ValueError, match="超限"): update.download(update.RULES_PAGE, "page")


def test_legacy_raw_snapshot_is_not_extended_or_replaced(repo):
    from ipg_pipeline.snapshot import update_output
    with pytest.raises(ValueError): update_output(repo, repo / "snapshots")
    with pytest.raises(ValueError): update_output(repo, repo / "dist")
    with pytest.raises(ValueError): update_output(repo, repo / "outputs/current-candidate/new-snapshot")


def test_current_pdf_reconciliation_preserves_all_ids_english_and_editorial_state():
    from ipg_pipeline.p3 import OFFICIAL_PDF
    project, notes, _, _ = update._inputs(ROOT, None)
    protected = copy.deepcopy(project["hashes"])
    parsed = parse_full_extraction(extract_full_pdf(OFFICIAL_PDF), frozen=False, expected_date="2024-09-23")
    reconciled = reconcile_update_document(parsed, [d for _, d in project["documents"]], project["registry"], version="test-same-official")
    result = inherit_editorial_state(project, reconciled, copy.deepcopy(project["manifest"]), notes)
    assert result["diff"]["summary"] == {"added": 0, "deleted": 0, "changed": 0, "englishChanged": 0, "renumbered": 0, "moved": 0, "reordered": 0}
    assert result["inheritance"]["officialIdsPreserved"] == 608
    assert result["inheritance"]["annotationsInherited"] == 330 and result["findings"] == []
    assert result["registry"] == project["registry"]
    update._unchanged(protected)


def test_update_parser_accepts_new_infraction_only_with_toc_and_appendix_evidence():
    from ipg_pipeline.p3 import OFFICIAL_PDF
    extraction = extract_full_pdf(OFFICIAL_PDF)
    lines = extraction["lines"]
    toc = next(i for i, line in enumerate(lines) if line["page"] == 2 and line["text"].startswith("2.6."))
    extra_toc = copy.deepcopy(lines[toc]); extra_toc.update(id="test-only-new-toc", text="2.7. Game Play Error — Synthetic Fixture .................................. 14")
    start = next(i for i, line in enumerate(lines) if line["page"] == 14 and line["text"].startswith("2.6."))
    end = next(i for i, line in enumerate(lines) if line["page"] == 15 and line["text"].startswith("3. "))
    added = copy.deepcopy([line for line in lines[start:end] if line["text"] != "14"])
    for i, line in enumerate(added): line["id"] = f"test-only-infraction-{i}"
    added[0]["text"] = "2.7. Game Play Error — Synthetic Fixture Warning"
    lines[end:end] = added
    lines.insert(toc + 1, extra_toc)
    row = next(i for i,line in enumerate(lines) if line["page"] == 30 and "Failure to Maintain Game State" in line["text"])
    extra_row = copy.deepcopy(lines[row]); extra_row.update(id="test-only-new-appendix-row", text="Synthetic Fixture Warning")
    lines.insert(row + 1, extra_row)
    parsed = parse_full_extraction(extraction, expected_date="2024-09-23", frozen=False)
    sections = parsed["document"]["sections"]
    assert len([s for s in sections if s["kind"] == "infraction"]) == 24
    new = next(s for s in sections if s["number"] == "2.7")
    rows = next(s for s in sections if s["number"] == "A")["components"][0]["groups"][0]["blocks"]
    assert next(b for b in rows if b["text"]["en"] == "Synthetic Fixture")["referenceId"] == new["id"]
    assert parsed["coverage"]["lineCount"] == len(lines)
    lines.remove(extra_row)
    with pytest.raises(FullParseError, match="appendix-a-infraction-coverage-mismatch"):
        parse_full_extraction(extraction, expected_date="2024-09-23", frozen=False)


@pytest.mark.parametrize("changed_hash", ["source", "target"])
def test_changed_note_hash_stales_even_without_previous_review(model, changed_hash):
    old, new, registry = model
    from ipg_pipeline.omegat import collect_units
    from ipg_pipeline.review import refresh_review_ledger
    display = {"values": {}}
    units = collect_units([("test.yaml", old)], display, release_relative="test")
    ledger = refresh_review_ledger(units, None, release_id="old", translation_revision="old")
    note = {"unitId": "block:ipg-b000002", "sourceHash": sha256_text("TEST ONLY beta"), "targetHash": sha256_text("合成译文 beta"),
            "releaseId": "old", "translationRevision": "old", "note": "TEST ONLY", "recordedAt": "2000-01-01", "stale": False}
    if changed_hash == "source":
        _blocks(new["document"])[1]["text"]["en"] = "TEST ONLY revised English"
    else:
        _blocks(old)[1]["text"]["zh"] = "合成：用户已修订译文"
    override = _operation(old, new, ["ipg-b000002"], [_blocks(new["document"])[1]["id"]])
    project = {"documents": [("test.yaml", old)], "display": display, "ledger": ledger}
    manifest = {"releaseId": "new", "versions": {"translation": {"revision": "new"}}, "scope": {"publicationAnnotations": {}}}
    result = inherit_editorial_state(project, reconcile_update_document(new, [old], registry, version="new", overrides=override), manifest, {"schemaVersion": 1, "notes": [note]})
    assert result["notes"]["notes"][0]["stale"]
    if changed_hash == "source":
        assert next(e for e in result["ledger"]["entries"] if e["unitId"] == "block:ipg-b000002")["status"] == "stale"


def test_download_successful_bounded_pdf(monkeypatch):
    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(PDF))}
        def geturl(self): return PDF_URL
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **k: Response(PDF)))
    assert update.download(PDF_URL, "pdf") == (PDF, PDF_URL, [])


def _nuxt_page(fault=None):
    # Compact synthetic copy of the observed Nuxt reference-pool shape; no WPN content.
    pool = [["ShallowReactive", 1], {"path": 2, "data": 3}, "/en/rules-documents",
            ["ShallowReactive", 4], {"downloadableDocuments": 5}, {"entries": 6}, [7],
            {"fields": 8, "sys": 14}, {"title": 9, "updated": 10, "cta": 11}, update.EXPECTED_TITLE,
            "2024-09-24", {"fields": 12}, {"link": 13}, PDF_URL,
            {"locale": 15, "contentType": 16}, "en", {"sys": 17}, {"id": 18}, "downloadableDocument"]
    if fault == "duplicate": pool[6] *= 2
    if fault == "missing": pool[6] = []
    if fault == "date": pool[10] = "2024-02-30"
    if fault == "url": pool[13] = "https://evil.example/a.pdf"
    if fault == "type": pool[18] = "unknown"
    if fault == "locale": pool[15] = "zh-hans"
    if fault == "reference": pool[8]["cta"] = 99999
    if fault == "collection": pool[4] = {"unknown": 5}
    if fault == "wrapper": pool[0] = ["ShallowReactive", 0]
    text = json.dumps(pool)
    if fault == "duplicate-field": text = text.replace('"entries": 6', '"entries": 6, "entries": 6')
    return f'<script type="application/json" id="__NUXT_DATA__">{text}</script>'


def test_complete_embedded_collection_finds_ipg_outside_visible_first_page():
    visible = _page().replace(update.EXPECTED_TITLE, "TEST ONLY other document")
    result = update.parse_rules_page(visible + _nuxt_page())
    assert result["pageUpdatedDate"] == "2024-09-24" and result["url"] == PDF_URL
    assert "effectiveDate" not in result


@pytest.mark.parametrize("fault", ["duplicate", "missing", "date", "url", "type", "locale", "reference", "collection", "wrapper", "duplicate-field"])
def test_embedded_page_anomalies_stop_without_falling_back_to_visible_card(fault):
    with pytest.raises(ValueError): update.parse_rules_page(_page("Sep 24, 2024") + _nuxt_page(fault))


def test_embedded_and_visible_card_must_agree():
    with pytest.raises(ValueError, match="不一致"):
        update.parse_rules_page(_page("Sep 23, 2024") + _nuxt_page())


def test_page_updated_date_is_not_official_pdf_effective_date(repo):
    report = update.check_official_update(repo,
        discover_release=lambda: (update.parse_rules_page(_nuxt_page()), _nuxt_page().encode()),
        downloader=lambda url, kind: (PDF, url, []), date_reader=lambda pdf: "2000-01-01",
        parser=lambda path, **kwargs: _temporary(load_project(repo)["documents"][0][1], sha256_bytes(PDF)),
        downloaded_at="2001-01-01T00:00:00Z")
    assert report["status"] == "no-update" and report["official"]["effectiveDate"] == "2000-01-01"
    assert report["official"]["pageUpdatedDate"] == "2024-09-24"


def test_effective_date_comes_from_pdf_not_filename_or_webpage():
    from ipg_pipeline.p3 import OFFICIAL_PDF
    assert update.pdf_effective_date(OFFICIAL_PDF.read_bytes()) == "2024-09-23"
