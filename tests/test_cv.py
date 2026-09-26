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
