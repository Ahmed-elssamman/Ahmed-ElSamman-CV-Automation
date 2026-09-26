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
        commands = []
        for index, line in enumerate(text.splitlines()):
            line = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append(f'BT /F1 10 Tf 30 {760-index*16} Td ({line}) Tj ET')
        stream.set_data('\n'.join(commands).encode())
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


def test_distinct_new_source_merges_matching_facts(tmp_path, monkeypatch):
    import workai.profile as module
    extraction = {"text": SAMPLE_TEXT, "pages": [{"page": 1, "text": SAMPLE_TEXT, "links": []}]}
    monkeypatch.setattr(module, 'extract_pdf', lambda _: extraction)
    source = tmp_path / 'first.pdf'
    create_text_pdf(source, 'First source document preserves its exact original content in the immutable ingestion archive.')
    first = ingest_pdfs(tmp_path, [source])
    second = tmp_path / 'second.pdf'
    create_text_pdf(second, 'Second source document has different exact original content; normalization must reconcile conflicts.')
    merged = ingest_pdfs(tmp_path, [second])
    assert merged['experience'][0]['title'] == first['experience'][0]['title']
    assert len(merged['experience']) == 1
    assert len(merged['source_ids']) == 2
    assert len({item['source_id'] for item in merged['evidence']['/skills/0']}) == 2
    assert merged['conflicts'] == []
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


def extraction(text=SAMPLE_TEXT):
    return {'text': text, 'pages': [{'page': 1, 'text': text, 'links': []}]}


def test_alternate_heading_stacked_role_and_skill_layout():
    text = SAMPLE_TEXT.replace('SUMMARY', 'Professional Summary:').replace('EXPERIENCE', 'Work Experience:')
    text = text.replace('EDUCATION', 'Academic Background:').replace('SKILLS', 'Technical Skills:')
    text = text.replace('Frontend Engineer                                  Remote / Cairo\nExample Company                              November 2024 - Present',
                        'Frontend Engineer\nExample Company\nNovember 2024 - Present')
    text = text.replace('Angular, TypeScript, REST APIs', 'Angular\nTypeScript; REST APIs')
    text = text.replace('Angular Course                                      2025 \\ Example Academy', 'Angular Course | Example Academy | 2025')
    profile = normalize_profile(extraction(text), 'sha256:alternate')
    assert profile['experience'][0]['company'] == 'Example Company'
    assert profile['experience'][0]['title'] == 'Frontend Engineer'
    assert profile['experience'][0]['location'] is None
    assert profile['skills'] == ['Angular', 'TypeScript', 'REST APIs']
    assert profile['certifications'][0]['issuer'] == 'Example Academy'
    assert validate_profile(profile)['valid']


def test_initial_sources_combine_complementary_facts_with_provenance():
    from workai.profile import reconcile_profiles
    first = normalize_profile(extraction(), 'sha256:first')
    second = normalize_profile(extraction(SAMPLE_TEXT.replace('Angular, TypeScript, REST APIs', 'Angular, React')
                                         .replace('ahmed@example.test', 'ahmed@example.test https://github.com/fixture')), 'sha256:second')
    merged = reconcile_profiles([first, second])
    assert merged['skills'] == ['Angular', 'TypeScript', 'REST APIs', 'React']
    assert merged['contact']['github'] == 'https://github.com/fixture'
    assert not any(u['field'] == 'contact.github' for u in merged['unknowns'])
    assert len(merged['experience']) == 1
    assert {e['source_id'] for e in merged['experience'][0]['evidence']} == {'sha256:first', 'sha256:second'}
    assert merged['evidence']['/skills/3'][0]['source_id'] == 'sha256:second'
    assert first['skills'] == ['Angular', 'TypeScript', 'REST APIs']
    assert validate_profile(merged)['valid']


def test_initial_scalar_and_employment_conflicts_withhold_claims():
    from workai.profile import reconcile_profiles
    first = normalize_profile(extraction(), 'sha256:first')
    second = normalize_profile(extraction(SAMPLE_TEXT.replace('ahmed@example.test', 'other@example.test')
                                         .replace('November 2024', 'November 2023')), 'sha256:second')
    merged = reconcile_profiles([first, second])
    assert merged['contact']['email'] is None
    assert merged['experience'] == []
    assert {c['field'] for c in merged['conflicts']} == {'contact.email', 'experience.experience-1'}
    assert all(c['canonical_status'] == 'UNKNOWN' and c['status'] == 'NEEDS_USER_INPUT' for c in merged['conflicts'])
    assert merged['conflicts'][1]['values'][1]['value']['start_date'] == '2023-11'
    assert merged['education']
    assert validate_profile(merged)['valid']


def test_later_pdf_cannot_replace_canonical_or_user_facts():
    from workai.profile import reconcile_profiles
    first = normalize_profile(extraction(), 'sha256:first')
    first['availability']['notice_period'] = '20 days'
    first['contact']['email'] = 'approved@example.test'
    first['evidence']['/contact/email'] = [{'source_id': 'user:approved', 'quote': 'approved@example.test', 'status': 'user_provided'}]
    second = normalize_profile(extraction(SAMPLE_TEXT.replace('November 2024', 'November 2023')), 'sha256:second')
    merged = reconcile_profiles([second], canonical=first)
    assert merged['contact']['email'] == 'approved@example.test'
    assert merged['experience'][0] == first['experience'][0]
    assert merged['availability']['notice_period'] == '20 days'
    assert all(c['canonical_status'] == 'retained' for c in merged['conflicts'])


def test_two_pdf_ingestion_is_versioned_and_idempotent(tmp_path, monkeypatch):
    import workai.profile as module
    first, second = tmp_path / 'one.pdf', tmp_path / 'two.pdf'
    create_text_pdf(first, 'First distinct original source')
    create_text_pdf(second, 'Second distinct original source')
    monkeypatch.setattr(module, 'extract_pdf', lambda p: extraction(SAMPLE_TEXT if p.name.endswith('01.pdf') else SAMPLE_TEXT.replace('REST APIs\n\nCERTIFICATIONS', 'React\n\nCERTIFICATIONS')))
    merged = ingest_pdfs(tmp_path, [first, second])
    assert len(merged['source_ids']) == 2
    assert 'React' in merged['skills']
    assert ingest_pdfs(tmp_path, [second, first]) == merged
    canonical = tmp_path / 'data/master-cv/profile.yaml'
    assert (canonical.parent / 'revisions' / hashlib.sha256(canonical.read_bytes()).hexdigest() / 'profile.yaml').read_bytes() == canonical.read_bytes()
    third = tmp_path / 'three.pdf'
    create_text_pdf(third, 'Third distinct original source')
    with pytest.raises(ProfileError, match='SOURCE_LIMIT_EXCEEDED'):
        ingest_pdfs(tmp_path, [third])
    assert len(list((tmp_path / 'data/source-cv').glob('cv-source-*.pdf'))) == 2


def test_duplicate_bytes_reuse_archive_even_with_different_filename(tmp_path, monkeypatch):
    import workai.profile as module
    first, second = tmp_path / 'one.pdf', tmp_path / 'renamed.pdf'
    create_text_pdf(first, 'Same source in two differently named files')
    second.write_bytes(first.read_bytes())
    monkeypatch.setattr(module, 'extract_pdf', lambda _: extraction())
    profile = ingest_pdfs(tmp_path, [first, second])
    assert len(profile['source_ids']) == 1
    assert len(list((tmp_path / 'data/source-cv').glob('cv-source-*.pdf'))) == 1


def test_local_ocr_dependency_error_is_explicit(tmp_path, monkeypatch):
    import workai.profile as module
    path = tmp_path / 'scan.pdf'
    create_text_pdf(path)
    monkeypatch.setattr(module.shutil, 'which', lambda _: None)
    with pytest.raises(ProfileError, match='OCR_DEPENDENCY_MISSING.*pdftoppm.*tesseract'):
        extract_pdf(path, ocr=True)


def test_ocr_artifact_review_then_ingestion_and_integrity(tmp_path, monkeypatch):
    import workai.profile as module
    path = tmp_path / 'scan.pdf'
    create_text_pdf(path)
    monkeypatch.setattr(module, '_ocr_page', lambda *args: SAMPLE_TEXT)
    with pytest.raises(ProfileError, match='OCR_REVIEW_REQUIRED'):
        ingest_pdfs(tmp_path, [path], ocr=True)
    assert not (tmp_path / 'data/master-cv/profile.yaml').exists()
    manifest_path = tmp_path / 'data/source-cv/manifest.json'
    source = json.loads(manifest_path.read_text())['sources'][0]
    raw_path = tmp_path / 'data/source-cv' / (source['archive_name'] + '.extracted.json')
    raw = json.loads(raw_path.read_text())
    assert raw['verification'] == 'ocr_unverified'
    with pytest.raises(ProfileError, match='OCR_REVIEW_REQUIRED'):
        normalize_profile(raw, source['source_id'])
    options = dict(reviewed_pages=[{'page': 1, 'text': SAMPLE_TEXT}], reviewer='user:fixture-reviewer',
                   expected_extraction_sha256=source['extraction_sha256'], note='Compared every line with source page 1.')
    reviewed = module.review_ocr(tmp_path, source['source_id'], **options)
    assert module.review_ocr(tmp_path, source['source_id'], **options) == reviewed
    profile = ingest_pdfs(tmp_path, [path])
    assert profile['experience'][0]['title'] == 'Frontend Engineer'
    assert profile['evidence']['/skills/0'][0]['transcription_review']['reviewer'] == 'user:fixture-reviewer'
    assert raw_path.read_text() == json.dumps(raw, ensure_ascii=False, indent=2) + '\n'
    reviewed_path = tmp_path / 'data/source-cv' / (source['archive_name'] + '.reviewed.json')
    reviewed_path.chmod(0o644)
    reviewed_path.write_text('{}')
    source = json.loads(manifest_path.read_text())['sources'][0]
    with pytest.raises(ProfileError, match='SOURCE_INTEGRITY_FAILED'):
        module.load_source_extraction(tmp_path, source)


def test_ocr_review_rejects_stale_hash_and_missing_pages(tmp_path, monkeypatch):
    import workai.profile as module
    path = tmp_path / 'scan.pdf'
    create_text_pdf(path)
    monkeypatch.setattr(module, '_ocr_page', lambda *args: SAMPLE_TEXT)
    with pytest.raises(ProfileError, match='OCR_REVIEW_REQUIRED'):
        ingest_pdfs(tmp_path, [path], ocr=True)
    source = json.loads((tmp_path / 'data/source-cv/manifest.json').read_text())['sources'][0]
    with pytest.raises(ProfileError, match='OCR_REVIEW_CONFLICT'):
        module.review_ocr(tmp_path, source['source_id'], reviewed_pages=[], reviewer='reviewer',
                          expected_extraction_sha256='stale', note='Compared source')
    with pytest.raises(ProfileError, match='every page'):
        module.review_ocr(tmp_path, source['source_id'], reviewed_pages=[], reviewer='reviewer',
                          expected_extraction_sha256=source['extraction_sha256'], note='Compared source')


def test_profile_optimistic_edit_rejects_stale_hash(tmp_path, sample_profile):
    from workai.profile import _persist_profile, apply_user_clarification
    _persist_profile(tmp_path, sample_profile)
    path = tmp_path / 'data/master-cv/profile.yaml'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    apply_user_clarification(tmp_path, {'availability.notice_period': '20 days'}, source_id='user:first',
                             source_text='Notice period is 20 days', expected_sha256=digest)
    before = path.read_bytes()
    with pytest.raises(ProfileError, match='PROFILE_CONCURRENT_EDIT'):
        apply_user_clarification(tmp_path, {'availability.notice_period': '10 days'}, source_id='user:stale',
                                 source_text='Notice period is 10 days', expected_sha256=digest)
    assert path.read_bytes() == before
    assert not (tmp_path / 'data/approved/user-stale.json').exists()


def test_failed_atomic_replacement_preserves_canonical_bytes(tmp_path, sample_profile, monkeypatch):
    import workai.profile as module
    module._persist_profile(tmp_path, sample_profile)
    path = tmp_path / 'data/master-cv/profile.yaml'
    before = path.read_bytes()
    changed = copy.deepcopy(sample_profile)
    changed['availability']['notice_period'] = '30 days'
    actual_replace = module.os.replace

    def fail_canonical(source, target):
        if Path(target) == path:
            raise OSError('Simulated write interruption')
        return actual_replace(source, target)

    monkeypatch.setattr(module.os, 'replace', fail_canonical)
    with pytest.raises(OSError, match='Simulated write interruption'):
        module._persist_profile(tmp_path, changed, expected_sha256=hashlib.sha256(before).hexdigest())
    assert path.read_bytes() == before
    assert not list(path.parent.glob('.workai-*'))


def test_clarification_resolution_retains_conflict_history(tmp_path):
    from workai.profile import _persist_profile, apply_user_clarification, reconcile_profiles
    first = normalize_profile(extraction(), 'sha256:first')
    second = normalize_profile(extraction(SAMPLE_TEXT.replace('ahmed@example.test', 'other@example.test')), 'sha256:second')
    merged = reconcile_profiles([first, second])
    _persist_profile(tmp_path, merged)
    result = apply_user_clarification(tmp_path, {'contact.email': 'approved@example.test'},
                                     source_id='user:resolve-email', source_text='My email is approved@example.test',
                                     resolved_unknowns=['contact.email'])
    assert result['contact']['email'] == 'approved@example.test'
    assert result['conflicts'][0]['status'] == 'RESOLVED_BY_USER'
    assert result['conflicts'][0]['values'][0]['value'] == 'ahmed@example.test'


def test_real_local_ocr_recognizes_raster_pdf_but_requires_review(tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess
    from pypdf.generic import NumberObject

    local = Path(__file__).resolve().parents[1] / '.tools/ocr/usr'
    if not shutil.which('tesseract') and (local / 'bin/tesseract').is_file():
        monkeypatch.setenv('PATH', str(local / 'bin') + os.pathsep + os.environ.get('PATH', ''))
        monkeypatch.setenv('TESSDATA_PREFIX', str(local / 'share/tesseract/tessdata'))
    if not all(shutil.which(tool) for tool in ('tesseract', 'pdftoppm')):
        pytest.skip('Optional real OCR requires local Poppler and Tesseract')
    source = tmp_path / 'text.pdf'
    create_text_pdf(source, SAMPLE_TEXT.replace('•', '-'))
    subprocess.run(['pdftoppm', '-singlefile', '-r', '180', str(source), str(tmp_path / 'scan')], check=True, capture_output=True)
    # Embed raw RGB pixels into a PDF: deliberately no selectable text remains.
    ppm = (tmp_path / 'scan.ppm').read_bytes()
    magic, dimensions, maximum, pixels = ppm.split(b'\n', 3)
    assert magic == b'P6' and maximum == b'255'
    width, height = (int(v) for v in dimensions.split())
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    raster = DecodedStreamObject()
    raster.set_data(pixels)
    raster.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                   NameObject('/Width'): NumberObject(width), NameObject('/Height'): NumberObject(height),
                   NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Scan'): writer._add_object(raster.flate_encode())})})
    stream = DecodedStreamObject()
    stream.set_data(b'q 600 0 0 800 0 0 cm /Scan Do Q')
    page[NameObject('/Contents')] = writer._add_object(stream)
    scan = tmp_path / 'raster.pdf'
    with scan.open('wb') as handle:
        writer.write(handle)
    with pytest.raises(ProfileError, match='OCR_REQUIRED'):
        extract_pdf(scan)
    result = extract_pdf(scan, ocr=True)
    assert result['verification'] == 'ocr_unverified'
    assert 'Angular' in result['text']
    assert 'Frontend Engineer' in result['text']
    assert result['pages'][0]['method'] == 'ocr'
    with pytest.raises(ProfileError, match='OCR_REVIEW_REQUIRED'):
        normalize_profile(result, 'sha256:real-ocr-fixture')
