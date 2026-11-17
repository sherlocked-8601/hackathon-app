import asyncio
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from app import render_service
from app.render_proxy import Proxy,Lease
from app.discovery_worker.sources import resolve_public,FetchError


@pytest.mark.parametrize('url',['http://example.com','https://localhost','https://127.0.0.1','https://169.254.169.254/latest/meta-data/',
                              'https://[::1]','https://[::ffff:127.0.0.1]','https://224.0.0.1','https://name.internal',
                              'https://user:secret@example.com','https://example.com:8443','https://2130706433'])
def test_renderer_rejects_nonpublic_and_credential_urls(url):
    assert not render_service.valid_url(url)


def test_renderer_auth_and_private_url_fail_before_browser(monkeypatch):
    monkeypatch.setenv('RENDER_API_KEY','x'*32)
    client=TestClient(render_service.app)
    assert client.post('/render',json={'url':'https://example.com'}).status_code==401
    assert client.post('/render',json={'url':'https://127.0.0.1'},headers={'Authorization':'Bearer '+'x'*32}).status_code==422


def test_renderer_failure_reports_stage_without_secret_and_releases_busy(monkeypatch, caplog):
    monkeypatch.setenv('RENDER_API_KEY', 'x'*32)
    class FailedClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            raise RuntimeError('secret-token-do-not-log')
    monkeypatch.setattr(render_service.httpx, 'AsyncClient', FailedClient)
    client = TestClient(render_service.app)
    response = client.post('/render', json={'url': 'https://example.com'}, headers={'Authorization':'Bearer '+'x'*32})
    assert response.status_code == 503
    assert response.json()['detail'] == {'code':'render_unavailable','stage':'proxy_lease','error_type':'RuntimeError'}
    assert not render_service.busy
    assert 'stage=proxy_lease' in caplog.text
    assert 'secret-token' not in response.text + caplog.text


def test_proxy_dns_rebinding_mixed_answers_are_rejected():
    with pytest.raises(FetchError):
        resolve_public('https://example.com',lambda _:['93.184.216.34','127.0.0.1'])


def test_proxy_requires_lease_and_auth_and_blocks_private_tunnels():
    async def check():
        proxy=Proxy('k'*32)
        server=await asyncio.start_server(proxy.handle,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        async def request(raw):
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            writer.write(raw.encode());await writer.drain()
            response=await asyncio.wait_for(reader.read(),2)
            writer.close();await writer.wait_closed()
            return response.decode()
        try:
            assert '401 Unauthorized' in await request('POST /lease HTTP/1.1\r\nHost: proxy\r\n\r\n')
            assert '407 Proxy' in await request('CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n')
            raw=await request('POST /lease HTTP/1.1\r\nAuthorization: Bearer '+'k'*32+'\r\n\r\n')
            lease=json.loads(raw.split('\r\n\r\n')[1])
            auth=base64.b64encode((lease['lease']+':'+lease['password']).encode()).decode()
            for host in ('127.0.0.1:443','169.254.169.254:443','example.com:80'):
                assert '403 Forbidden' in await request(f'CONNECT {host} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n')
            assert '409 Conflict' in await request('POST /lease HTTP/1.1\r\nAuthorization: Bearer '+'k'*32+'\r\n\r\n')
            await request('DELETE /lease/'+lease['lease']+' HTTP/1.1\r\nAuthorization: Bearer '+'k'*32+'\r\n\r\n')
            assert not proxy.leases
        finally:
            server.close();await server.wait_closed()
    asyncio.run(check())
