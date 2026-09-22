from unittest.mock import Mock, patch

import pytest

from kdzwy_receipt_uploader.commands.login_companies import authenticated_user_payload, wait_authenticated_context


@pytest.mark.parametrize('payload', [None, {}, {'data': {}}, {'data': {'id': 1}, 'code': 401},
                                    {'data': {'id': 1}, 'success': False}, {'data': 'login'}])
def test_rejects_unauthenticated_payload(payload):
    assert not authenticated_user_payload(payload)


def test_accepts_authenticated_payload():
    assert authenticated_user_payload({'code': 200, 'data': {'id': 1}})


def test_login_popup_and_changed_homepage_path():
    page = Mock(url='https://gj.kdzwy.com/login')
    popup = Mock(url='https://vip1-gj.kdzwy.com/new-home')
    page.is_closed.return_value = popup.is_closed.return_value = False
    context = Mock(pages=[page, popup])
    response = context.request.get.return_value
    response.ok = True
    response.json.return_value = {'data': {'id': 1}}
    assert wait_authenticated_context(context, timeout=2, progress=Mock()) == 'https://vip1-gj.kdzwy.com'
    assert context.request.get.call_args.args[0].endswith('/guanjia/user/info')
    response.dispose.assert_called_once()


def test_wrong_session_never_passes_even_at_old_success_url():
    page = Mock(url='https://vip1-gj.kdzwy.com/acct-web/guanjia/')
    page.is_closed.return_value = False
    context = Mock(pages=[page])
    context.request.get.return_value.json.return_value = {'code': 401, 'data': None}
    with patch('kdzwy_receipt_uploader.commands.login_companies.time.monotonic', side_effect=[0,0,0,0,3,3]):
        with pytest.raises(RuntimeError, match='有效登录会话'):
            wait_authenticated_context(context, timeout=2, progress=Mock())


def test_closed_browser_fails_immediately():
    with pytest.raises(RuntimeError, match='浏览器已关闭'):
        wait_authenticated_context(Mock(pages=[]), timeout=2, progress=Mock())


def test_wrong_password_ends_wait_without_retry_delay():
    page = Mock(url='https://gj.kdzwy.com/')
    page.is_closed.return_value = False
    page.locator.return_value.inner_text.return_value = '账号或密码错误'
    context = Mock(pages=[page])
    context.request.get.return_value.ok = False
    with pytest.raises(RuntimeError, match='账号或密码错误'):
        wait_authenticated_context(context, timeout=180, progress=Mock())
    page.wait_for_timeout.assert_not_called()
