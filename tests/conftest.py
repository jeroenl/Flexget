from __future__ import unicode_literals, division, absolute_import, print_function
from builtins import object
from builtins import str
import logging

import itertools

import os
import sys
import yaml
from contextlib import contextmanager

import mock
import pytest
from path import Path
from vcr import VCR

import flexget.logger
from flexget.manager import Manager
from flexget.plugin import load_plugins
from flexget.task import Task, TaskAbort
from flexget.webserver import User
from flexget.manager import Session
from flexget.api import app

log = logging.getLogger('tests')


VCR_CASSETTE_DIR = os.path.join(os.path.dirname(__file__), 'cassettes')
VCR_RECORD_MODE = os.environ.get('VCR_RECORD_MODE', 'once')

vcr = VCR(cassette_library_dir=VCR_CASSETTE_DIR, record_mode=VCR_RECORD_MODE)


# --- These are the public fixtures tests can ask for ---

@pytest.fixture(scope='class')
def config(request):
    """
    If used inside a test class, uses the `config` class attribute of the class.
    This is used by `manager` fixture, and can be parametrized.
    """
    return request.cls.config


@pytest.yield_fixture()
def manager(request, config, caplog, monkeypatch, filecopy):  # enforce filecopy is run before manager
    """
    Create a :class:`MockManager` for this test based on `config` argument.
    """
    if 'tmpdir' in request.fixturenames:
        config = config.replace('__tmp__', request.getfuncargvalue('tmpdir').strpath)
    try:
        mockmanager = MockManager(config, request.cls.__name__)
    except Exception:
        # Since we haven't entered the test function yet, pytest won't print the logs on failure. Print them manually.
        print(caplog.text())
        raise
    yield mockmanager
    mockmanager.shutdown()


@pytest.fixture()
def execute_task(manager):
    """
    A function that can be used to execute and return a named task in `config` argument.
    """

    def execute(task_name, abort=False, options=None):
        """
        Use to execute one test task from config.

        :param abort: If `True` expect (and require) this task to abort.
        """
        log.info('********** Running task: %s ********** ' % task_name)
        config = manager.config['tasks'][task_name]
        task = Task(manager, task_name, config=config, options=options)

        try:
            if abort:
                with pytest.raises(TaskAbort):
                    task.execute()
            else:
                task.execute()
        finally:
            try:
                task.session.close()
            except Exception:
                pass
        return task

    return execute


@pytest.yield_fixture()
def use_vcr(request, monkeypatch):
    """
    This fixture is applied automatically to any test using the `online` mark. It will record and playback network
    sessions using VCR.

    The record mode of VCR can be set using the VCR_RECORD_MODE environment variable when running tests.
    """
    if VCR_RECORD_MODE == 'off':
        yield None
    else:
        module = request.module.__name__.split('tests.')[-1]
        class_name = request.cls.__name__
        cassette_name = '.'.join([module, class_name, request.function.__name__])
        cassette_path = os.path.join(VCR_CASSETTE_DIR, cassette_name)
        online = True
        if vcr.record_mode == 'none':
            online = False
        elif vcr.record_mode == 'once':
            online = not os.path.exists(cassette_path)
        # If we are not going online, disable domain limiting during test
        if not online:
            log.debug('Disabling domain limiters during VCR playback.')
            monkeypatch.setattr('flexget.utils.requests.limit_domains', mock.Mock())
        with vcr.use_cassette(path=cassette_path) as cassette:
            yield cassette


@pytest.fixture()
def api_client(manager):
    with Session() as session:
        user = session.query(User).first()
        if not user:
            user = User(name='flexget', password='flexget')
            session.add(user)
            session.commit()
        return APIClient(user.token)

# --- End Public Fixtures ---


def pytest_configure(config):
    # register the filecopy marker
    config.addinivalue_line('markers',
        'filecopy(src, dst): mark test to copy a file from `src` to `dst` before running.'
        'online: mark a test that goes online. VCR will automatically be used.')


def pytest_runtest_setup(item):
    # Add the filcopy fixture to any test marked with filecopy
    if item.get_marker('filecopy'):
        item.fixturenames.append('filecopy')
    # Add the online marker to tests that will go online
    if item.get_marker('online'):
        item.fixturenames.append('use_vcr')
    else:
        item.fixturenames.append('no_requests')


@pytest.yield_fixture()
def filecopy(request):
    out_files = []
    marker = request.node.get_marker('filecopy')
    if marker is not None:
        sources, dst = marker.args
        if isinstance(sources, str):
            sources = [sources]
        if 'tmpdir' in request.fixturenames:
            dst = dst.replace('__tmp__', request.getfuncargvalue('tmpdir').strpath)
        dst = Path(dst)
        for f in itertools.chain(*(Path().glob(src) for src in sources)):
            dest_path = dst
            if dest_path.isdir():
                dest_path = dest_path / f.basename()
            f.copy(dest_path)
            out_files.append(dest_path)
    yield
    if out_files:
        for f in out_files:
            try:
                f.remove()
            except OSError as e:
                print("couldn't remove %s: %s" % (f, e))


@pytest.fixture()
def no_requests(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request",
                        mock.Mock(side_effect=Exception('Online tests should use @pytest.mark.online')))


@pytest.fixture(scope='session', autouse=True)
def setup_once(pytestconfig, request):
    os.chdir(os.path.join(pytestconfig.rootdir.strpath, 'tests'))
    flexget.logger.initialize(True)
    m = MockManager('tasks: {}', 'init')  # This makes sure our template environment is set up before any tests are run
    m.shutdown()
    logging.getLogger().setLevel(logging.DEBUG)
    load_plugins()


@pytest.fixture(autouse=True)
def setup_loglevel(pytestconfig, caplog):
    # set logging level according to pytest verbosity
    level = logging.DEBUG
    if pytestconfig.getoption('verbose') == 1:
        level = flexget.logger.TRACE
    elif pytestconfig.getoption('quiet') == 1:
        level = logging.INFO
    logging.getLogger().setLevel(level)
    caplog.setLevel(level)


class CrashReport(Exception):
    pass


class MockManager(Manager):
    unit_test = True

    def __init__(self, config_text, config_name, db_uri=None):
        self.config_text = config_text
        self._db_uri = db_uri or 'sqlite:///:memory:'
        super(MockManager, self).__init__(['execute'])
        self.config_name = config_name
        self.database_uri = self._db_uri
        log.debug('database_uri: %s' % self.database_uri)
        self.initialize()

    def find_config(self, *args, **kwargs):
        """
        Override configuration loading
        """
        self.config_base = os.path.dirname(os.path.abspath(sys.path[0]))

    def load_config(self):
        """
        Just load our config from the text passed in on init
        """
        config = yaml.safe_load(self.config_text) or {}
        self.update_config(config)

    # no lock files with unit testing
    @contextmanager
    def acquire_lock(self, **kwargs):
        self._has_lock = True
        yield

    def release_lock(self):
        pass

    def crash_report(self):
        # We don't want to silently swallow crash reports during unit tests
        log.error('Crash Report Traceback:', exc_info=True)
        raise CrashReport('Crash report created during unit test, check log for traceback.')


class APIClient(object):
    def __init__(self, api_key):
        self.api_key = api_key
        self.client = app.test_client()

    def _append_header(self, key, value, kwargs):
        if 'headers' not in kwargs:
            kwargs['headers'] = {}

        kwargs['headers'][key] = value

    def json_post(self, *args, **kwargs):
        self._append_header('Content-Type', 'application/json', kwargs)
        if kwargs.get('auth', True):
            self._append_header('Authorization', 'Token %s' % self.api_key, kwargs)
        return self.client.post(*args, **kwargs)

    def json_put(self, *args, **kwargs):
        self._append_header('Content-Type', 'application/json', kwargs)
        if kwargs.get('auth', True):
            self._append_header('Authorization', 'Token %s' % self.api_key, kwargs)
        return self.client.put(*args, **kwargs)

    def get(self, *args, **kwargs):
        if kwargs.get('auth', True):
            self._append_header('Authorization', 'Token %s' % self.api_key, kwargs)

        return self.client.get(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if kwargs.get('auth', True):
            self._append_header('Authorization', 'Token %s' % self.api_key, kwargs)

        return self.client.delete(*args, **kwargs)