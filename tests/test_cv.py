import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from workai.cv import CVError, build_master, find_compiler, latex_escape, render_latex, sanitize, tailor_cv
from test_profile import sample_profile


def test_latex_escape_blocks_executable_input():
    rendered = latex_escape(r'50% & {x} \input{/etc/passwd} $value #1_a')
    assert r'\input' not in rendered
    assert r'\textbackslash{}input' in rendered
    assert r'50\%' in rendered
    assert r'\&' in rendered


def test_render_uses_known_facts_only(sample_profile):
    result = render_latex(sample_profile)
    assert 'Angular' in result
    assert 'Docker' not in result
    assert 'Native' not in result
    assert '\\begin{tabular}' not in result
    assert 'Example Company' in result


def test_fabricated_skill_rejected_even_with_unrelated_citation(sample_profile):
    sample_profile['skills'][0] = 'Docker'
    with pytest.raises(CVError, match='Unsupported skill'):
        render_latex(sample_profile)


def test_fabricated_bullet_rejected(sample_profile):
    sample_profile['experience'][0]['bullets'].append('Led 500 engineers.')
    with pytest.raises(CVError, match='Unsupported experience assertion'):
        render_latex(sample_profile)


def test_fabricated_dates_rejected(sample_profile):
    sample_profile['experience'][0]['start_date'] = '2015-11'
    with pytest.raises(CVError, match='Unsupported employment date'):
        render_latex(sample_profile)


def test_safe_filename():
    assert '/' not in sanitize('../../Acme & Co: Angular Developer')
    assert len(sanitize('a' * 1000)) <= 85


def test_compile_tailor_and_immutable_versions(tmp_path, sample_profile, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    try:
        compiler = find_compiler(project)
    except CVError:
        pytest.skip('Install Tectonic/XeLaTeX to run actual PDF compilation integration')
    monkeypatch.setenv('WORKAI_TECTONIC', compiler)
    directory = tmp_path / 'data/master-cv'
    directory.mkdir(parents=True)
    (directory / 'profile.yaml').write_text(yaml.safe_dump(sample_profile))
    master = build_master(tmp_path)
    assert master['quality_gates']['selectable_text']
    assert Path(master['pdf_path']).read_bytes().startswith(b'%PDF')
    job = {'id': 'fixture-job', 'company': 'Example & Co', 'position': 'Angular Engineer', 'description': 'Angular and Docker required', 'required_skills': ['Angular', 'Docker']}
    version = tailor_cv(tmp_path, job, {})
    assert Path(version['pdf_path']).exists()
    assert 'Docker' not in Path(version['tex_path']).read_text()
    assert tailor_cv(tmp_path, job, {})['version_id'] == version['version_id']
    changed = {**job, 'description': 'Different vacancy text'}
    assert tailor_cv(tmp_path, changed, {})['version_id'] != version['version_id']
    pdf = Path(version['pdf_path'])
    pdf.chmod(0o644)
    pdf.write_bytes(b'%PDF tampered')
    with pytest.raises(CVError, match='CV_VERSION_INTEGRITY_FAILED'):
        tailor_cv(tmp_path, job, {})


def test_missing_source_archive_blocks_pdf(tmp_path, sample_profile):
    sample_profile['source_ids'] = ['sha256:missing']
    directory = tmp_path / 'data/master-cv'
    directory.mkdir(parents=True)
    (directory / 'profile.yaml').write_text(yaml.safe_dump(sample_profile))
    with pytest.raises(CVError, match='SOURCE_EVIDENCE_UNAVAILABLE'):
        build_master(tmp_path)


@pytest.fixture
def canonical_with_pending_ocr(tmp_path, monkeypatch):
    import workai.profile as module
    from test_profile import SAMPLE_TEXT, create_text_pdf, extraction

    first, second = tmp_path / 'selectable.pdf', tmp_path / 'pending-scan.pdf'
    create_text_pdf(first, 'The original selectable candidate source with complete professional facts.')
    create_text_pdf(second)

    def extract(path, **kwargs):
        result = extraction(SAMPLE_TEXT)
        if path.name == 'cv-source-02.pdf':
            result['verification'] = 'ocr_unverified'
            result['pages'][0]['method'] = 'ocr'
        return result

    monkeypatch.setattr(module, 'extract_pdf', extract)
    canonical = module.ingest_pdfs(tmp_path, [first])
    with pytest.raises(module.ProfileError, match='OCR_REVIEW_REQUIRED'):
        module.ingest_pdfs(tmp_path, [second], ocr=True)
    manifest = json.loads((tmp_path / 'data/source-cv/manifest.json').read_text())
    return tmp_path, canonical, manifest


def test_pending_unreferenced_ocr_does_not_block_canonical_evidence(canonical_with_pending_ocr):
    from workai.cv import _assert_evidence
    root, canonical, manifest = canonical_with_pending_ocr
    assert manifest['sources'][1]['source_id'] not in canonical['source_ids']
    _assert_evidence(canonical, root)
    assert 'Example Company' in render_latex(canonical)


def test_referenced_ocr_still_requires_review(canonical_with_pending_ocr):
    from workai.cv import _assert_evidence
    from workai.profile import ProfileError
    root, canonical, manifest = canonical_with_pending_ocr
    canonical['evidence']['/skills/0'][0]['source_id'] = manifest['sources'][1]['source_id']
    with pytest.raises(ProfileError, match='OCR_REVIEW_REQUIRED'):
        _assert_evidence(canonical, root)


def test_unused_archive_tampering_still_fails_integrity(canonical_with_pending_ocr):
    from workai.cv import _assert_evidence
    root, canonical, manifest = canonical_with_pending_ocr
    archive = root / 'data/source-cv' / manifest['sources'][1]['archive_name']
    archive.chmod(0o644)
    archive.write_bytes(b'changed source bytes')
    with pytest.raises(CVError, match='checksum mismatch'):
        _assert_evidence(canonical, root)


def test_fact_reference_requires_manifest_even_without_source_id_list(tmp_path, sample_profile):
    from workai.cv import _assert_evidence
    sample_profile['source_ids'] = []
    sample_profile['evidence']['/skills/0'][0]['source_id'] = 'sha256:missing'
    with pytest.raises(CVError, match='SOURCE_EVIDENCE_UNAVAILABLE'):
        _assert_evidence(sample_profile, tmp_path)


def test_canonical_source_missing_from_manifest_is_rejected(canonical_with_pending_ocr):
    from workai.cv import _assert_evidence
    root, canonical, manifest = canonical_with_pending_ocr
    manifest['sources'] = manifest['sources'][1:]
    (root / 'data/source-cv/manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(CVError, match='SOURCE_EVIDENCE_UNAVAILABLE.*absent'):
        _assert_evidence(canonical, root)
