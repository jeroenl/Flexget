# -*- coding: utf-8 -*-
from __future__ import unicode_literals, division, absolute_import
from builtins import *  # noqa pylint: disable=unused-import, redefined-builtin
from future.utils import PY3

import mock
import pytest

from flexget.manager import Session
from flexget.plugins.input.npo_watchlist import NPOWatchlist


@pytest.mark.online
class TestNpoWatchlistInfo(object):
    config = """
        tasks:
          test:
            npo_watchlist:
              email: '8h3ga3+7nzf7xueal70o@sharklasers.com'
              password: 'Fl3xg3t!'
    """

    def test_npowatchlist_lookup(self, execute_task):
        """npo_watchlist: Test npo watchlist lookup (ONLINE)"""

        task = execute_task('test')

        entry = task.find_entry(url='https://www.npostart.nl/the-fourth-estate/29-05-2018/VPWON_1280492')  # s01e01
        assert entry['npo_url'] == 'https://www.npostart.nl/the-fourth-estate/VPWON_1280496'
        assert entry['npo_name'] == 'The Fourth Estate'
        assert entry['npo_description'] == 'Filmmaker Liz Garbus volgt het reilen en zeilen binnen de The New York Times. De ongekende toegang tot de redactie van het Amerikaanse dagblad levert bijzondere interviews op met de redacteuren en verslaggevers die het nieuws rond president Trump brengen. Garbus laat vanuit het perspectief van correspondenten van het Witte Huis, onderzoeksjournalisten en redacteuren bij The New York Times de uitdagingen, overwinningen en valkuilen zien van de verslaggeving over president Trump, die de vrije pers de oorlog heeft verklaard.'
        assert entry['npo_runtime'] == '87'

        assert task.find_entry(url='https://www.npostart.nl/als-de-dijken-breken-official-trailer-2016/26-10-2016/POMS_EO_5718640') is None  # a trailer, that should not be listed


@pytest.mark.online
class TestNpoWatchlistLanguageTheTVDBLookup(object):
    config = """
        tasks:
          test:
            npo_watchlist:
              email: '8h3ga3+7nzf7xueal70o@sharklasers.com'
              password: 'Fl3xg3t!'
            thetvdb_lookup: yes
    """

    def test_tvdblang_lookup(self, execute_task):
        """npo_watchlist: Test npo_watchlist tvdb language lookup (ONLINE)"""

        task = execute_task('test')

        entry = task.find_entry(url='https://www.npostart.nl/zaak-van-je-leven/04-07-2017/POW_03548461')  # s01e01
        assert entry['npo_language'] == 'nl'
        assert entry['language'] == 'nl'
        assert entry['tvdb_id'] == 313833
        assert entry['tvdb_language'] == 'nl'
