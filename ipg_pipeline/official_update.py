"""Safe official discovery and isolated native IPG update preparation."""
from __future__ import annotations

import argparse
import copy
import io
import json
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path

import pdfplumber

from .builder import build_outputs
from .core import ROOT, dump_yaml, load_json, load_yaml, sha256_bytes, validate_registry, validate_schema
from .full_parser import FullParseError, english_date, parse_full_pdf
from .official_inheritance import inherit_editorial_state
from .production import _hashes, _unchanged, _validate_project, load_project, validate_output
from .reconcile import reconcile_update_document
from .snapshot import create_snapshot, json_bytes, update_output, verify_snapshot
from .validation import validate_release


RULES_PAGE = "https://wpn.wizards.com/en/rules-documents"
EXPECTED_TITLE = "Magic Infraction Procedure Guide"
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024


def validate_url(url: str, kind: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    host = "wpn.wizards.com" if kind == "page" else "media.wizards.com"
    try:
        valid = (parsed.scheme == "https" and parsed.hostname == host and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    path = urllib.parse.unquote(parsed.path)
    if (not valid or "\\" in path or ".." in path.split("/") or any(ord(c) < 32 or ord(c) == 127 for c in urllib.parse.unquote(url))
            or kind == "page" and path not in {"/en/rules-documents", "/en/document/magic-infraction-procedure-guide"}
            or kind == "pdf" and not path.lower().endswith(".pdf")):
        raise ValueError(f"非法官方 {kind} URL：{url}")


class RulesPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.stack, self.capture = [], None, [], None
        self.payloads, self.payload = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "__NUXT_DATA__":
            if self.payload is not None or attrs.get("type") != "application/json":
                raise ValueError("官方页面数据脚本结构异常")
            self.payload = ""
        classes = attrs.get("class", "") or ""
        if re.search(r"downloadableDocument(?![a-zA-Z])", classes, re.I):
            if self.row is not None:
                raise ValueError("官方文档条目异常嵌套")
            self.row = {"title": "", "date": "", "links": [], "fields": []}
            self.stack = []
        if self.row is None:
            return
        if tag not in {"img", "br", "input", "hr", "meta", "link", "source"}:
            self.stack.append(tag)
        field = "title" if "titletext" in classes.lower() else "date" if "updated" in classes.lower() else None
        if field:
            if field in self.row["fields"] or self.capture is not None:
                raise ValueError("官方文档标题或日期字段重复")
            self.row["fields"].append(field)
            self.capture = (field, len(self.stack))
        if tag == "a" and attrs.get("href"):
            self.row["links"].append(attrs["href"])

    def handle_data(self, data):
        if self.payload is not None:
            self.payload += data
        if self.row is not None and self.capture:
            self.row[self.capture[0]] += data

    def handle_endtag(self, tag):
        if tag == "script" and self.payload is not None:
            self.payloads.append(self.payload)
            self.payload = None
        if self.row is None or tag in {"img", "br", "input", "hr", "meta", "link", "source"}:
            return
        if not self.stack or self.stack[-1] != tag:
            raise ValueError("官方文档 HTML 结构不匹配")
        if self.capture and self.capture[1] == len(self.stack):
            self.capture = None
        self.stack.pop()
        if not self.stack:
            self.rows.append(self.row)
            self.row = None


def _nuxt_documents(payload: str) -> list[dict]:
    """Read only the known rules-page document collection, not arbitrary strings."""
    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("官方页面数据含重复字段")
            value[key] = item
        return value
    pool = json.loads(payload, object_pairs_hook=unique_keys)
    if not isinstance(pool, list) or len(pool) > 50000:
        raise ValueError("官方页面引用池结构异常")
    def ref(index):
        if type(index) is not int or not 0 <= index < len(pool):
            raise ValueError("官方页面引用越界")
        value = pool[index]
        for _ in range(3):
            if isinstance(value, list) and len(value) == 2 and value[0] in ("Reactive", "ShallowReactive"):
                index = value[1]
                if type(index) is not int or not 0 <= index < len(pool):
                    raise ValueError("官方页面包装引用越界")
                value = pool[index]
            else:
                return value
        raise ValueError("官方页面引用包装异常")
    try:
        root = ref(0)
        if ref(root["path"]) != "/en/rules-documents":
            raise ValueError("官方页面数据不是规则文档集合")
        collection = ref(ref(root["data"])["downloadableDocuments"])
        entries = ref(collection["entries"])
        if not isinstance(entries, list) or len(entries) != len(set(entries)):
            raise ValueError("官方文档集合异常或重复引用")
        rows = []
        for index in entries:
            entry = ref(index)
            fields = ref(entry["fields"])
            title = ref(fields["title"])
            if title != EXPECTED_TITLE:
                continue
            system = ref(entry["sys"])
            if ref(system["locale"]) != "en" or ref(ref(ref(system["contentType"])["sys"])["id"]) != "downloadableDocument":
                raise ValueError("IPG 条目的类型或语言不合法")
            updated = ref(fields["updated"])
            if not isinstance(updated, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated):
                raise ValueError("IPG 网页更新时间不合法")
            date.fromisoformat(updated)
            url = ref(ref(ref(fields["cta"])["fields"])["link"])
            validate_url(url, "pdf")
            rows.append({"title": title, "pageUpdatedDate": updated, "url": url})
        return rows
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("官方文档数据结构无法识别") from error


def parse_rules_page(html: str, page_url: str = RULES_PAGE) -> dict:
    validate_url(page_url, "page")
    parser = RulesPageParser()
    parser.feed(html)
    parser.close()
    if parser.row is not None or parser.payload is not None or len(parser.payloads) > 1:
        raise ValueError("官方文档条目未闭合")
    matches = [r for r in parser.rows if " ".join(r["title"].split()) == EXPECTED_TITLE]
    rows = _nuxt_documents(parser.payloads[0]) if parser.payloads else []
    if len(matches) > 1 or parser.payloads and len(rows) != 1 or not parser.payloads and len(matches) != 1:
        raise ValueError(f"必须精确找到一个 IPG 条目，实际 {len(rows) if parser.payloads else len(matches)}")
    visible = None
    if matches:
        row = matches[0]
        links = {urllib.parse.urljoin(page_url, link) for link in row["links"]}
        if len(links) != 1 or set(row["fields"]) != {"title", "date"}:
            raise ValueError("IPG 条目的日期或 PDF 链接不唯一")
        url = next(iter(links))
        validate_url(url, "pdf")
        visible = {"title": EXPECTED_TITLE, "pageUpdatedDate": english_date(row["date"].strip()), "url": url}
    if rows and visible and rows[0] != visible:
        raise ValueError("网页 IPG 卡片与完整文档集合不一致")
    return {"pageUrl": page_url, **(rows[0] if rows else visible)}


def pdf_effective_date(pdf: bytes) -> str:
    """The page's `updated` is publication metadata, not the PDF Effective date."""
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        if not 1 <= len(document.pages) <= 100:
            raise ValueError("官方 PDF 页数超出支持范围")
        dates = re.findall(r"^Effective (.+)$", document.pages[0].extract_text() or "", re.M)
    if len(dates) != 1:
        raise ValueError("官方 PDF 必须包含唯一的有效日期")
    return english_date(dates[0])


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, kind):
        self.kind, self.hops = kind, []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.kind)  # Validate BEFORE following any redirect.
        self.hops.append(newurl)
        if len(self.hops) > 5:
            raise ValueError("官方下载重定向过多")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, kind: str) -> tuple[bytes, str, list[str]]:
    validate_url(url, kind)
    maximum = MAX_PAGE_BYTES if kind == "page" else MAX_PDF_BYTES
    handler = SafeRedirect(kind)
    opener = urllib.request.build_opener(handler)
    request = urllib.request.Request(url, headers={"User-Agent": "ipg-zh-local-update/1", "Accept-Encoding": "identity"})
    with opener.open(request, timeout=30) as response:
        final = response.geturl()
        validate_url(final, kind)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("不接受压缩的官方下载响应")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > maximum):
            raise ValueError("官方下载长度异常或超限")
        chunks, size = [], 0
        while True:
            chunk = response.read(min(65536, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise ValueError("官方下载超限")
        if length is not None and size != int(length):
            raise ValueError("官方下载截断或长度不一致")
    payload = b"".join(chunks)
    if kind == "pdf" and not payload.startswith(b"%PDF-"):
        raise ValueError("官方下载不是 PDF")
    return payload, final, handler.hops


def discover() -> tuple[dict, bytes]:
    page, final, hops = download(RULES_PAGE, "page")
    official = parse_rules_page(page.decode("utf-8-sig"), final)
    official["pageRedirects"] = hops
    return official, page


def _inputs(root: Path, override_path: Path | None) -> tuple[dict, dict | None, dict | None, dict]:
    project = load_project(root)
    report = _validate_project(project, profile="candidate")
    if not report["valid"]:
        raise ValueError("当前源结构验证失败，停止官方更新")
    revision = project["manifest"]["versions"]["translation"]["revision"]
    notes_path = root / f"review/translation-notes/{revision}.json"
    additional = [root / "src/ipg/mapping-overrides.yaml", notes_path, root / f"review/actions/{revision}.yaml"]
    if override_path:
        additional.append(override_path)
    project["hashes"].update(_hashes(additional))
    notes = load_json(notes_path) if notes_path.exists() else None
    if notes is not None:
        errors = validate_schema(notes, root / "schema/translation-notes.schema.json")
        if errors or notes["releaseId"] != project["manifest"]["releaseId"] or notes["translationRevision"] != revision:
            raise ValueError("基础翻译批注版本或结构不合法")
        if len({n["unitId"] for n in notes["notes"]}) != len(notes["notes"]):
            raise ValueError("基础翻译批注存在重复身份")
    if project["ledger"] is not None:
        if (validate_schema(project["ledger"], root / "schema/review-ledger.schema.json")
                or project["ledger"]["releaseId"] != project["manifest"]["releaseId"]
                or project["ledger"]["translationRevision"] != revision):
            raise ValueError("基础审校账本格式不合法；不会自动修复")
    overrides = load_json(override_path) if override_path else None
    if overrides is not None and validate_schema(overrides, root / "schema/update-overrides.schema.json"):
        raise ValueError("更新 override 结构不合法")
    implementation = {f"ipg_pipeline/{p.name}": sha256_bytes(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))}
    project["hashes"].update(_hashes(list(Path(__file__).parent.glob("*.py"))))
    return project, notes, overrides, implementation


def _prepare(root: Path, project: dict, notes: dict | None, overrides: dict | None,
             implementation: dict, official: dict, pdf: bytes, page: bytes = b"",
             parser=parse_full_pdf) -> dict:
    target = {k: official[k] for k in ("effectiveDate", "pdfSha256", "url", "pageUrl")}
    if "pageUpdatedDate" in official:
        target["pageUpdatedDate"] = official["pageUpdatedDate"]
    base = project["manifest"]
    override = overrides or {"schemaVersion": 1, "baseReleaseId": base["releaseId"],
        "targetOfficial": {k: target[k] for k in ("effectiveDate", "pdfSha256")}, "mappings": []}
    if override["baseReleaseId"] != base["releaseId"] or override["targetOfficial"] != {k: target[k] for k in ("effectiveDate", "pdfSha256")}:
        raise ValueError("更新 override 绑定的版本不一致")
    relative_hashes = {Path(p).relative_to(root).as_posix(): h for p, h in project["hashes"].items() if Path(p).is_relative_to(root)}
    context = {"baseReleaseId": base["releaseId"], "baseFiles": relative_hashes, "targetOfficial": target,
        "implementation": implementation, "updateOverridesSha256": sha256_bytes(json_bytes(override)),
        "dependencies": {name: version(name) for name in ("pdfplumber", "pdfminer.six", "PyYAML", "jsonschema")}}
    context_hash = sha256_bytes(json_bytes(context))
    changed = official["pdfSha256"] != base["versions"]["official"]["pdfSha256"]
    same_day = changed and official["effectiveDate"] == base["versions"]["official"]["effectiveDate"]
    manifest = copy.deepcopy(base)
    if changed:
        revision = base["versions"]["translation"]["revision"] + "-update-" + context_hash[:12]
        manifest["versions"]["translation"]["revision"] = revision
        manifest["releaseId"] = f"ipg-{official['effectiveDate']}-pdf{official['pdfSha256'][:16]}__ann-{base['versions']['annotations']['sourceSha256'][:12]}__{revision}"
        manifest["versions"]["official"] = {k: official[k] for k in ("effectiveDate", "pdfSha256", "downloadedAt", "url")}
        manifest["publishable"] = False
    with tempfile.TemporaryDirectory(prefix="ipg-update-parse-") as temporary:
        path = Path(temporary) / "IPG_EN.pdf"
        path.write_bytes(pdf)
        parsed = parser(path, expected_sha256=official["pdfSha256"], expected_date=official["effectiveDate"], frozen=False)
        reconciled = reconcile_update_document(parsed, [d for _, d in project["documents"]], project["registry"],
            version=manifest["releaseId"], overrides=override)
        inherited = inherit_editorial_state(project, reconciled, manifest, notes)
        errors = validate_registry(inherited["registry"])
        errors.extend(e for _, doc in inherited["documents"] for e in validate_schema(doc, root / "schema/ipg-source.schema.json"))
        errors.extend(validate_schema(manifest, root / "schema/ipg-manifest.schema.json"))
        if errors:
            raise ValueError("候选 schema/registry 验证失败：" + "; ".join(errors))
        migration = copy.deepcopy(project["migration"])
        migration["findings"].extend({"code": "unresolved-mapping", "detail": finding} for finding in inherited["findings"])
        candidate = validate_release(profile="candidate", manifest=manifest, documents=[d for _, d in inherited["documents"]],
            display_values=project["display"], migration_report=migration, review_ledger=inherited["ledger"], schema_dir=root / "schema")
        if not candidate["valid"]:
            raise ValueError("隔离候选结构验证失败")
        status = "same-day-repack" if same_day else "new-official-version" if changed else "no-update"
        report = {"schemaVersion": 1, "status": status, "changed": changed, "baseReleaseId": base["releaseId"],
            "official": official, "diff": inherited["diff"], "inheritance": inherited["inheritance"],
            "findings": inherited["findings"], "candidateValidation": candidate,
            "migrationLineage": {"baseReleaseId": base["releaseId"], "authority": project["migration"]["authority"],
                "meaning": "Historical legacy coverage only; not proof that changed English has been translated or reviewed."},
            "requiresApproval": changed, "currentReleaseUnchanged": True}
        payloads = {"official/IPG_EN.pdf": pdf, "parsed/official.json": json_bytes(parsed),
            "reports/update.json": json_bytes(report), "reports/pdf-coverage.json": json_bytes(parsed["coverage"]),
            "inputs/update-overrides.json": json_bytes(override), "proposed/id-registry.yaml": dump_yaml(inherited["registry"]).encode("utf-8"),
            "proposed/mapping-overrides.yaml": (root / "src/ipg/mapping-overrides.yaml").read_bytes(),
            "reports/unresolved-annotations.json": json_bytes(inherited["unresolvedAnnotations"]),
            "reports/deleted-review-records.json": json_bytes(inherited["deletedReviewRecords"]),
            "reports/reconciliation.json": json_bytes(reconciled["reconciliation"])}
        if page:
            payloads["official/rules-page.html"] = page
        for path_text, digest in project["hashes"].items():
            path = Path(path_text)
            if digest is not None and path.is_relative_to(root):
                payloads["inputs/base/" + path.relative_to(root).as_posix()] = path.read_bytes()
        payloads.update({"implementation/" + name: (Path(__file__).parent / Path(name).name).read_bytes() for name in implementation})
        if changed:
            payloads["candidate/source/manifest.yaml"] = dump_yaml(manifest).encode("utf-8")
            for name, document in inherited["documents"]:
                payloads["candidate/source/" + name] = dump_yaml(document).encode("utf-8")
            revision = manifest["versions"]["translation"]["revision"]
            payloads[f"candidate/review/status/{revision}.json"] = json_bytes(inherited["ledger"])
            payloads[f"candidate/review/translation-notes/{revision}.json"] = json_bytes(inherited["notes"])
            preview = Path(temporary) / "preview"
            build_outputs(preview, manifest, [d for _, d in inherited["documents"]], project["display"], candidate=True, profile="candidate", source_root=root)
            if not validate_output(preview / "rules.json", root)["valid"]:
                raise ValueError("候选输出独立 schema 验证失败")
            for file in preview.iterdir():
                payloads["candidate/preview/" + file.name] = file.read_bytes()
        payloads["reports/review.md"] = (f"# 官方 IPG 更新审阅（不可自动提升）\n\n状态：{status}\n\n"
            f"官方日期：{official['effectiveDate']}\nPDF：{official['url']}\nSHA-256：{official['pdfSha256']}\n\n"
            f"结构变化：{json.dumps(inherited['diff']['summary'], ensure_ascii=False)}\n\n"
            f"继承：{json.dumps(inherited['inheritance'], ensure_ascii=False)}\n\n"
            f"未决 finding：{len(inherited['findings'])}；详情见 update.json。\n\n"
            "本快照不会切换指针、改写当前源、账本、OmegaT 或 dist。中文旧译不等于新英文已审译文。\n").encode("utf-8")
        _unchanged(project["hashes"])
        snapshot = create_snapshot(root, context=context, official=official, payloads=payloads, versions=manifest["versions"])
    stored = load_json(snapshot / "reports/update.json")
    return {**stored, "snapshot": snapshot.relative_to(root).as_posix()}


def check_official_update(root: Path = ROOT, *, override_path: Path | None = None,
                          discover_release=discover, downloader=download, parser=parse_full_pdf,
                          downloaded_at: str | None = None, date_reader=pdf_effective_date) -> dict:
    root = root.resolve()
    project, notes, overrides, implementation = _inputs(root, override_path)
    official, page = discover_release()
    validate_url(official["pageUrl"], "page")
    pdf, final, redirects = downloader(official["url"], "pdf")
    validate_url(final, "pdf")
    if len(pdf) > MAX_PDF_BYTES or not pdf.startswith(b"%PDF-"):
        raise ValueError("官方 PDF 非法或超限")
    official.update(url=final, pdfRedirects=redirects, pdfSha256=sha256_bytes(pdf),
        effectiveDate=date_reader(pdf),
        downloadedAt=downloaded_at or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if official["effectiveDate"] < project["manifest"]["versions"]["official"]["effectiveDate"]:
        raise ValueError("官方版本日期回退，拒绝生成候选")
    _unchanged(project["hashes"])
    return _prepare(root, project, notes, overrides, implementation, official, pdf, page, parser)


def snapshot_current(root: Path = ROOT) -> dict:
    root = root.resolve()
    project, notes, overrides, implementation = _inputs(root, None)
    official = {**project["manifest"]["versions"]["official"], "pageUrl": RULES_PAGE, "title": EXPECTED_TITLE,
                "pageRedirects": [], "pdfRedirects": []}
    pdf_dir = root / "snapshots/official" / official["effectiveDate"] / official["pdfSha256"]
    return _prepare(root, project, notes, overrides, implementation, official, next(pdf_dir.glob("*.pdf")).read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ipg-check-official-update")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args(argv)
    try:
        report = check_official_update(args.project_root, override_path=args.project_root / args.overrides if args.overrides else None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"官方更新已停止：{error}")
        if isinstance(error, FullParseError):
            print(json.dumps(error.findings, ensure_ascii=False, indent=2))
        return 1
