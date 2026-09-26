import copy
import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from workai.profile import ProfileError, extract_pdf, ingest_pdfs, normalize_profile, validate_profile


SAMPLE_TEXT = '''Ahmed ELsamman
ahmed@example.test  +20 100 000 0000  Cairo, Egypt

SUMMARY
Frontend Engineer building Angular applications.

EXPERIENCE
Frontend Engineer                                  Remote / Cairo
Example Company                              November 2024 - Present
• Built Angular applications with TypeScript.
• Integrated REST APIs and improved responsive user interfaces.

EDUCATION
Bachelor of Science in Computer Science
Example University

PROJECTS
Example App Developed an Angular application with TypeScript.

SKILLS
Angular, TypeScript, REST APIs

CERTIFICATIONS
Angular Course                                      2025 \\ Example Academy

LANGUAGES
Arabic • English
'''


@pytest.fixture
def sample_profile():
    extraction = {"text": SAMPLE_TEXT, "pages": [{"page": 1, "text": SAMPLE_TEXT, "links": []}]}
    return normalize_profile(extraction, "user:test-fixture")


def create_text_pdf(path, text=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    if text:
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 30 700 Td (' + text + ') Tj ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as handle:
        writer.write(handle)


def test_extraction_reads_pdf_text(tmp_path):
    path = tmp_path / 'cv.pdf'
    create_text_pdf(path, 'A candidate factual text with more than eighty characters of professional evidence for reliable PDF ingestion.')
    result = extract_pdf(path)
    assert 'professional evidence' in result['text']
    assert result['pages'][0]['page'] == 1


def test_scanned_pdf_requires_verified_ocr(tmp_path):
    path = tmp_path / 'scanned.pdf'
    create_text_pdf(path)
    with pytest.raises(ProfileError, match='OCR_REQUIRED'):
        extract_pdf(path)


def test_normalization_preserves_facts_and_unknowns(sample_profile):
    assert sample_profile['experience'][0]['start_date'] == '2024-11'
    assert sample_profile['experience'][0]['current'] is True
    assert len(sample_profile['experience'][0]['bullets']) == 2
    assert sample_profile['legal']['sponsorship'] is None
    assert sample_profile['languages'][1]['proficiency'] is None
    assert sample_profile['preferences']['salary']['Egypt']['period'] is None
    assert 'Node.js' not in sample_profile['skills']
    assert validate_profile(sample_profile)['valid']


def test_missing_skill_provenance_rejected(sample_profile):
    sample_profile['skills'].append('Docker')
    assert not validate_profile(sample_profile)['valid']


def test_date_validation_rejects_reversed_range(sample_profile):
    sample_profile['experience'][0].update(end_date='2020-01', current=False)
    assert not validate_profile(sample_profile)['valid']


def test_archive_integrity_and_idempotency(tmp_path, monkeypatch):
    import workai.profile as module
    source = tmp_path / 'original.pdf'
    create_text_pdf(source, 'A sufficiently long source sentence that lets us demonstrate immutable source archives and idempotent operation.')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    extraction = {"text": SAMPLE_TEXT, "pages": [{"page": 1, "text": SAMPLE_TEXT, "links": []}]}
    monkeypatch.setattr(module, 'extract_pdf', lambda _: extraction)
    profile = ingest_pdfs(tmp_path, [source])
    assert ingest_pdfs(tmp_path, [source]) == profile
    manifest = json.loads((tmp_path / 'data/source-cv/manifest.json').read_text())
    assert len(manifest['sources']) == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    archive = tmp_path / 'data/source-cv/cv-source-01.pdf'
    archive.chmod(0o644)
    archive.write_bytes(b'tampered')
    with pytest.raises(ProfileError, match='SOURCE_INTEGRITY_FAILED'):
        ingest_pdfs(tmp_path, [source])


def test_distinct_new_source_preserved_requires_reconciliation(tmp_path, monkeypatch):
    import workai.profile as module
    extraction = {"text": SAMPLE_TEXT, "pages": [{"page": 1, "text": SAMPLE_TEXT, "links": []}]}
    monkeypatch.setattr(module, 'extract_pdf', lambda _: extraction)
    source = tmp_path / 'first.pdf'
    create_text_pdf(source, 'First source document preserves its exact original content in the immutable ingestion archive.')
    first = ingest_pdfs(tmp_path, [source])
    second = tmp_path / 'second.pdf'
    create_text_pdf(second, 'Second source document has different exact original content; normalization must reconcile conflicts.')
    with pytest.raises(ProfileError, match='NEW_SOURCE_REVIEW_REQUIRED'):
        ingest_pdfs(tmp_path, [second])
    assert module.load_profile(tmp_path) == first
    assert len(json.loads((tmp_path / 'data/source-cv/manifest.json').read_text())['sources']) == 2


def test_user_clarification_preserves_profile_and_exact_approval(tmp_path, sample_profile):
    from workai.profile import _persist_profile, apply_user_clarification
    _persist_profile(tmp_path, sample_profile)
    original = (tmp_path / 'data/master-cv/profile.yaml').read_bytes()
    text = 'Notice Period:\n20 days.\nEgypt: authorized without sponsorship.'
    updates = {'availability.notice_period': '20 days', 'legal.work_authorization': {'Egypt': True},
               'legal.sponsorship': {'Egypt': False}}
    result = apply_user_clarification(tmp_path, updates, source_id='user:clarification-test', source_text=text,
                                     resolved_unknowns=['availability.notice_period'])
    assert result['availability']['notice_period'] == '20 days'
    assert result['legal']['work_authorization']['Egypt'] is True
    assert result['legal']['citizenship'] is None
    assert not any(u['field'] == 'availability.notice_period' for u in result['unknowns'])
    assert (tmp_path / result['revision']['previous_profile_path']).read_bytes() == original
    approval = json.loads((tmp_path / 'data/approved/user-clarification-test.json').read_text())
    assert approval['verbatim_user_text'] == text
    assert apply_user_clarification(tmp_path, updates, source_id='user:clarification-test', source_text=text,
                                   resolved_unknowns=['availability.notice_period']) == result
    with pytest.raises(ProfileError, match='APPROVAL_CONFLICT'):
        apply_user_clarification(tmp_path, {'availability.notice_period': '10 days'},
                                source_id='user:clarification-test', source_text='10 days')
