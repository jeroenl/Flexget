from __future__ import unicode_literals, division, absolute_import
from urlparse import urlparse
import logging
import requests

from flexget import plugin
from flexget.event import event
from flexget.entry import Entry

log = logging.getLogger('sickbeard')


class Sickbeard(object):

    schema = {
        'type': 'object',
        'properties': {
            'base_url': {'type': 'string'},
            'port': {'type': 'number', 'default': 80},
            'api_key': {'type': 'string'},
            'include_ended': {'type': 'boolean', 'default': True},
            'only_monitored': {'type': 'boolean', 'default': False}
        },
        'required': ['api_key', 'base_url'],
        'additionalProperties': False
    }

    def on_task_input(self, task, config):
        '''
        This plugin returns ALL of the shows monitored by Sickbeard.
        This includes both ongoing and ended.
        Syntax:

        sickbeard:
          base_url=<value>
          port=<value>
          api_key=<value>

        Options base_url and api_key are required.

        Use with input plugin like discover and/or cofnigure_series.
        Example:

        download-tv-task:
          configure_series:
            settings:
              quality:
                - 720p
            from:
              sickbeard:
                base_url: http://localhost
                port: 8531
                api_key: MYAPIKEY1123
          discover:
            what:
              - emit_series: yes
            from:
              torrentz: any
          download:
            /download/tv

        Note that when using the configure_series plugin with Sickbeard
        you are basically synced to it, so removing a show in Sickbeard will
        remove it in flexget as well,which good be positive or negative,
        depending on your usage.
        '''
        parsedurl = urlparse(config.get('base_url'))
        url = '%s://%s:%s%s/api/%s/?cmd=shows' % (parsedurl.scheme, parsedurl.netloc,
                                                  config.get('port'), parsedurl.path, config.get('api_key'))
        json = task.requests.get(url).json()
        entries = []
        for id, show in json['data'].items():
            if not show['paused'] or not config.get('only_monitored'):
                if config.get('include_ended') or show['status'] != 'Ended':
                    entry = Entry(title=show['show_name'],
                                  url='',
                                  series_name=show['show_name'],
                                  tvdb_id=show['tvdbid'],
                                  tvrage_id=show['tvrage_id'])
            if entry.isvalid():
                entries.append(entry)
            else:
                log.debug('Invalid entry created? %s' % entry)
            # Test mode logging
            if task.options.test: 
                log.info("Test mode. Entry includes:")
                log.info("    Title: %s" % entry["title"])
                log.info("    URL: %s" % entry["url"])
                log.info("    Show name: %s" % entry["series_name"])
                log.info("    TVDB ID: %s" % entry["tvdb_id"])
                log.info("    TVRAGE ID: %s" % entry["tvrage_id"])
                continue
        return entries


@event('plugin.register')
def register_plugin():
    plugin.register(Sickbeard, 'sickbeard', api_ver=2)
