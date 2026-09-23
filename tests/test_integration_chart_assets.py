import base64
import hashlib
import io
import tarfile
import pytest
from scripts.build_chart_assets import VERSION, ARCHIVE, unpack


def archive():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for path, content in [('package/dist/lightweight-charts.standalone.production.js', b'fixture js'), ('package/LICENSE', b'fixture license')]:
            info = tarfile.TarInfo(path); info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    payload = buffer.getvalue()
    meta = {'name':'lightweight-charts','version':VERSION,'dist':{'tarball':ARCHIVE,'integrity':'sha512-'+base64.b64encode(hashlib.sha512(payload).digest()).decode()}}
    return meta, payload


def test_chart_archive_identity_and_integrity():
    meta, data = archive()
    result = unpack(meta, data)
    assert result[f'lightweight-charts-{VERSION}.js'] == b'fixture js'
    assert result['lightweight-charts-LICENSE.txt'] == b'fixture license'
    meta['dist']['integrity'] = 'sha512-wrong'
    with pytest.raises(ValueError, match='INTEGRITY'): unpack(meta, data)


def test_chart_build_rejects_other_sources_and_versions():
    meta, data = archive()
    meta['dist']['tarball'] = 'https://evil.invalid/file'
    with pytest.raises(ValueError, match='SOURCE'): unpack(meta, data)
    meta, data = archive(); meta['version'] = 'latest'
    with pytest.raises(ValueError, match='IDENTITY'): unpack(meta, data)
