from __future__ import unicode_literals, division, absolute_import

import logging
from datetime import datetime, timedelta

from sqlalchemy import Table, Column, Integer, Float, Unicode, Boolean, DateTime, func
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import relation
from sqlalchemy.schema import ForeignKey
from sqlalchemy import func

from flexget import db_schema
from flexget.manager import Session
from flexget.utils import requests
from flexget.utils.database import with_session, text_date_synonym, json_synonym
from flexget.utils.simple_persistence import SimplePersistence

log = logging.getLogger('api_tvdb')
Base = db_schema.versioned_base('api_tvdb', 5)

# This is a FlexGet API key
persist = SimplePersistence('api_tvdb')


class TVDBRequest(object):
    API_KEY = '4D297D8CFDE0E105'
    BASE_URL = 'https://api-beta.thetvdb.com/'
    BANNER_URL = 'http://thetvdb.com/banners/'

    def __init__(self, username=None, password=None):
        self.username = username
        self.password = password
        self.auth_key = self.username if self.username else 'default'

    def get_auth_token(self, refresh=False):
        tokens = persist.get('auth_tokens')
        if not tokens:
            tokens = {'default': None}

        auth_token = tokens.get(self.auth_key)

        if not auth_token or refresh:
            data = {'apikey': TVDBRequest.API_KEY}
            if self.username:
                data['username'] = self.username
            if self.password:
                data['userpass'] = self.password

            log.debug('Authenticating to TheTVDB with %s' % (self.username if self.username else 'api_key'))

            tokens[self.auth_key] = requests.post(TVDBRequest.BASE_URL + 'login', json=data).json().get('token')
        persist['auth_tokens'] = tokens
        return tokens[self.auth_key]

    def _request(self, method, endpoint, **params):
        url = TVDBRequest.BASE_URL + endpoint
        headers = {'Authorization': 'Bearer %s' % self.get_auth_token()}
        data = params.pop('data', None)

        result = requests.request(method, url, params=params, headers=headers, raise_status=False, json=data)
        if result.status_code == 401:
            log.debug('Auth token expired, refreshing')
            headers['Authorization'] = 'Bearer %s' % self.get_auth_token(refresh=True)
            result = requests.request(method, url, params=params, headers=headers, raise_status=False, json=data)
        result.raise_for_status()
        result = result.json()

        if result.get('errors'):
            raise LookupError('Error processing request on tvdb: %s' % result.get('errors'))

        return result

    def get(self, endpoint, **params):
        result = self._request('get', endpoint, **params)
        return result.get('data')

    def post(self, endpoint, **params):
        result = self._request('post', endpoint, **params)
        return result.get('data')

    def put(self, endpoint, **params):
        result = self._request('put', endpoint, **params)
        return result.get('data')

    def delete(self, endpoint, **params):
        return self._request('delete', endpoint, **params)


@db_schema.upgrade('api_tvdb')
def upgrade(ver, session):
    if ver is None or ver <= 4:
        raise db_schema.UpgradeImpossible
    return ver


# association tables
genres_table = Table('tvdb_series_genres', Base.metadata,
                     Column('series_id', Integer, ForeignKey('tvdb_series.id')),
                     Column('genre_id', Integer, ForeignKey('tvdb_genres.id')))
Base.register_table(genres_table)


def _get_db_genres(genre_names):
    genres = []
    if genre_names:
        with Session() as session:
            for genre_name in genre_names:
                genre = session.query(TVDBGenre).filter(func.lower(TVDBGenre.name) == genre_name.lower()).first()
                if not genre:
                    genre = TVDBGenre(name=genre_name)
                    session.add(genre)
                    session.commit()
                genres.append({'id': genre.id, 'name': genre.name})

    return genres


class TVDBSeries(Base):
    __tablename__ = "tvdb_series"

    id = Column(Integer, primary_key=True, autoincrement=False)
    last_updated = Column(Integer)
    expired = Column(Boolean)
    name = Column(Unicode)
    language = Column(Unicode)
    rating = Column(Float)
    status = Column(Unicode)
    runtime = Column(Integer)
    airs_time = Column(Unicode)
    airs_dayofweek = Column(Unicode)
    content_rating = Column(Unicode)
    network = Column(Unicode)
    overview = Column(Unicode)
    imdb_id = Column(Unicode)
    zap2it_id = Column(Unicode)
    _banner = Column('banner', Unicode)

    _first_aired = Column('first_aired', DateTime)
    first_aired = text_date_synonym('_first_aired')
    _aliases = Column('aliases', Unicode)
    aliases = json_synonym('_aliases')
    _actors = Column('actors', Unicode)
    actors_list = json_synonym('_actors')
    _posters = Column('posters', Unicode)
    posters_list = json_synonym('_posters')

    _genres = relation('TVDBGenre', secondary=genres_table)
    genres = association_proxy('_genres', 'name')

    episodes = relation('TVDBEpisode', backref='series', cascade='all, delete, delete-orphan')

    def update(self):
        try:
            series = TVDBRequest().get('series/%s' % self.id)
        except requests.RequestException as e:
            raise LookupError('Error updating data from tvdb: %s' % e)

        self.id = series['id']
        self.language = 'en'
        self.last_updated = series['lastUpdated']
        self.name = series['seriesName']
        self.rating = float(series['siteRating']) if series['siteRating'] else 0.0
        self.status = series['status']
        self.runtime = int(series['runtime']) if series['runtime'] else 0
        self.airs_time = series['airsTime']
        self.airs_dayofweek = series['airsDayOfWeek']
        self.content_rating = series['rating']
        self.network = series['network']
        self.overview = series['overview']
        self.imdb_id = series['imdbId']
        self.zap2it_id = series['zap2itId']
        self.first_aired = series['firstAired']
        self.expired = False
        self.aliases = series['aliases']
        self._banner = series['banner']

        with Session() as session:
            search_strings = self.search_strings
            for name in set([self.name.lower()] + ([a.lower() for a in self.aliases] if self.aliases else [])):
                if name not in search_strings:
                    search_result = session.query(TVDBSearchResult).filter(func.lower(TVDBSearchResult.search) == name).first()
                    if not search_result:
                        search_result = TVDBSearchResult(search=name)
                    search_result.series_id = self.id
                    session.add(search_result)

        genres = _get_db_genres(series['genre'])
        self._genres = [TVDBGenre(**g) for g in genres]

        # Reset Actors and Posters so they can be lazy populated
        self._actors = None
        self._posters = None

    def __repr__(self):
        return '<TVDBSeries name=%s,tvdb_id=%s>' % (self.name, self.id)

    @property
    def banner(self):
        if self._banner:
            return TVDBRequest.BANNER_URL + self._banner

    @property
    def actors(self):
        return self.get_actors()

    @property
    def posters(self):
        return self.get_posters()

    def get_actors(self):
        if not self._actors:
            log.debug('Looking up actors for series %s' % self.name)
            try:
                actors_query = TVDBRequest().get('series/%s/actors' % self.id)
                self.actors_list = [a['name'] for a in actors_query] if actors_query else []
            except requests.RequestException as e:
                if None is not e.response and e.response.status_code == 404:
                    self.actors_list = []
                else:
                    raise LookupError('Error updating actors from tvdb: %s' % e)

        return self.actors_list

    def get_posters(self):
        if not self._posters:
            log.debug('Getting top 5 posters for series %s' % self.name)
            try:
                poster_query = TVDBRequest().get('series/%s/images/query' % self.id, keyType='poster')
                self.posters_list = [p['fileName'] for p in poster_query[:5]] if poster_query else []
            except requests.RequestException as e:
                if None is not e.response and e.response.status_code == 404:
                    self.posters_list = []
                else:
                    raise LookupError('Error updating posters from tvdb: %s' % e)

        return [TVDBRequest.BANNER_URL + p for p in self.posters_list]

    def to_dict(self):
        return {
            'tvdb_id': self.id,
            'last_updated': datetime.fromtimestamp(self.last_updated).strftime('%Y-%m-%d %H:%M:%S'),
            'expired': self.expired,
            'series_name': self.name,
            'language': self.language,
            'rating': self.rating,
            'status': self.status,
            'runtime': self.runtime,
            'airs_time': self.airs_time,
            'airs_dayofweek': self.airs_dayofweek,
            'content_rating': self.content_rating,
            'network': self.network,
            'overview': self.overview,
            'imdb_id': self.imdb_id,
            'zap2it_id': self.zap2it_id,
            'banner': self.banner,
            'posters': self.posters,
            'genres': [g for g in self.genres],
            'actors': self.actors,
            'first_aired': self.first_aired,
        }


class TVDBGenre(Base):
    __tablename__ = 'tvdb_genres'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Unicode, nullable=False, unique=True)


class TVDBEpisode(Base):
    __tablename__ = 'tvdb_episodes'

    id = Column(Integer, primary_key=True, autoincrement=False)
    expired = Column(Boolean)
    last_updated = Column(Integer)
    season_number = Column(Integer)
    episode_number = Column(Integer)
    absolute_number = Column(Integer)
    name = Column(Unicode)
    overview = Column(Unicode)
    rating = Column(Float)
    director = Column(Unicode)
    _image = Column(Unicode)
    _first_aired = Column('firstaired', DateTime)
    first_aired = text_date_synonym('_first_aired')

    series_id = Column(Integer, ForeignKey('tvdb_series.id'), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'expired': self.expired,
            'last_update': self.last_updated,
            'season_number': self.season_number,
            'episode_number': self.episode_number,
            'absolute_number': self.absolute_number,
            'episode_name': self.name,
            'overview': self.overview,
            'director': self.director,
            'rating': self.rating,
            'image': self.image,
            'first_aired': self.first_aired,
            'series_id': self.series_id
        }

    @property
    def image(self):
        if self._image:
            return TVDBRequest.BANNER_URL + self._image

    def update(self):
        try:
            episode = TVDBRequest().get('episodes/%s' % self.id)
        except requests.RequestException as e:
            raise LookupError('Error updating data from tvdb: %s' % e)

        self.id = episode['id']
        self.last_updated = episode['lastUpdated']
        self.season_number = episode['airedSeason']
        self.episode_number = episode['airedEpisodeNumber']
        self.absolute_number = episode['absoluteNumber']
        self.name = episode['episodeName']
        self.overview = episode['overview']
        self.director = episode['director']
        self._image = episode['filename']
        self.rating = episode['siteRating']
        self.first_aired = episode['firstAired']

    def __repr__(self):
        return '<TVDBEpisode series=%s,season=%s,episode=%s>' % \
               (self.series.name, self.season_number, self.episode_number)


class TVDBSearchResult(Base):
    __tablename__ = 'tvdb_search_results'

    id = Column(Integer, primary_key=True)
    search = Column(Unicode, nullable=False, unique=True)
    series_id = Column(Integer, ForeignKey('tvdb_series.id'), nullable=True)
    series = relation(TVDBSeries, backref='search_strings')


def find_series_id(name):
    """Looks up the tvdb id for a series"""
    try:
        series = TVDBRequest().get('search/series', name=name)
    except requests.RequestException as e:
        raise LookupError('Unable to get search results for %s: %s' % (name, e))

    series_list = []

    name = name.lower()

    for s in series:
        # Exact match
        series_name = s.get('seriesName')
        if series_name and series_name.lower() == name:
            return s['id']
        if s['firstAired']:
            series_list.append((s['firstAired'], s['id']))

    # If there is no exact match, sort by airing date and pick the latest
    if series_list:
        series_list.sort(key=lambda s: s[0], reverse=True)
        return series_list[0][1]
    else:
        raise LookupError('No results for `%s`' % name)


@with_session
def lookup_series(name=None, tvdb_id=None, only_cached=False, session=None):
    """
    Look up information on a series. Will be returned from cache if available, and looked up online and cached if not.

    Either `name` or `tvdb_id` parameter are needed to specify the series.
    :param unicode name: Name of series.
    :param int tvdb_id: TVDb ID of series.
    :param bool only_cached: If True, will not cause an online lookup. LookupError will be raised if not available
        in the cache.
    :param session: An sqlalchemy session to be used to lookup and store to cache. Commit(s) may occur when passing in
        a session. If one is not supplied it will be created.

    :return: Instance of :class:`TVDBSeries` populated with series information. If session was not supplied, this will
        be a detached from the database, so relationships cannot be loaded.
    :raises: :class:`LookupError` if series cannot be looked up.
    """
    if not (name or tvdb_id):
        raise LookupError('No criteria specified for tvdb lookup')

    log.debug('Looking up tvdb information for %s %s', name, tvdb_id)

    series = None

    def id_str():
        return '<name=%s,tvdb_id=%s>' % (name, tvdb_id)

    if tvdb_id:
        series = session.query(TVDBSeries).filter(TVDBSeries.id == tvdb_id).first()
    if not series and name:
        found = session.query(TVDBSearchResult).filter(func.lower(TVDBSearchResult.search) == name.lower()).first()
        if found and found.series:
                series = found.series
    if series:
        # Series found in cache, update if cache has expired.
        if not only_cached:
            mark_expired(session=session)
        if not only_cached and series.expired:
            log.verbose('Data for %s has expired, refreshing from tvdb', series.name)
            try:
                series.update()
            except LookupError as e:
                log.warning('Error while updating from tvdb (%s), using cached data.', e.args[0])
        else:
            log.debug('Series %s information restored from cache.' % id_str())
    else:
        if only_cached:
            raise LookupError('Series %s not found from cache' % id_str())
        # There was no series found in the cache, do a lookup from tvdb
        log.debug('Series %s not found in cache, looking up from tvdb.', id_str())
        if tvdb_id:
            series = TVDBSeries(id=tvdb_id)
            series.update()
            if series.name:
                session.merge(series)
        elif name:
            tvdb_id = find_series_id(name)
            if tvdb_id:
                series = session.query(TVDBSeries).filter(TVDBSeries.id == tvdb_id).first()
                if not series:
                    series = TVDBSeries()
                    series.id = tvdb_id
                    series.update()
                    session.merge(series)

                # Add search result to cache
                search_result = session.query(TVDBSearchResult).filter(func.lower(TVDBSearchResult.search) == name.lower()).first()
                if not search_result:
                    search_result = TVDBSearchResult(search=name.lower())
                    search_result.series_id = tvdb_id
                    session.add(search_result)

    if not series:
        raise LookupError('No results found from tvdb for %s' % id_str())
    if not series.name:
        raise LookupError('Tvdb result for series does not have a title.')

    return series


@with_session
def lookup_episode(name=None, season_number=None, episode_number=None, absolute_number=None,
                   tvdb_id=None, only_cached=False, session=None):
    """
    Look up information on an episode. Will be returned from cache if available, and looked up online and cached if not.

    Either `name` or `tvdb_id` parameter are needed to specify the series.
    Either `seasonnum` and `episodedum`, `absolutenum`, or `airdate` are required to specify episode number.
    :param unicode name: Name of series episode belongs to.
    :param int tvdb_id: TVDb ID of series episode belongs to.
    :param int season_number: Season number of episode.
    :param int episode_number: Episode number of episode.
    :param int absolute_number: Absolute number of episode.
    :param bool only_cached: If True, will not cause an online lookup. LookupError will be raised if not available
        in the cache.
    :param session: An sqlalchemy session to be used to lookup and store to cache. Commit(s) may occur when passing in
        a session. If one is not supplied it will be created, however if you need to access relationships you should
        pass one in.

    :return: Instance of :class:`TVDBEpisode` populated with series information.
    :raises: :class:`LookupError` if episode cannot be looked up.
    """
    # First make sure we have the series data
    series = lookup_series(name=name, tvdb_id=tvdb_id, only_cached=only_cached, session=session)

    if not series:
        LookupError('Series %s (%s) not found from' % (name, tvdb_id))

    ep_description = series.name
    query_params = {}
    episode = session.query(TVDBEpisode).filter(TVDBEpisode.series_id == series.id)

    if absolute_number:
        episode = episode.filter(TVDBEpisode.absolute_number == absolute_number)
        query_params['absoluteNumber'] = absolute_number
        ep_description = '%s absNo: %s' % (ep_description, absolute_number)

    if season_number:
        episode = episode.filter(TVDBEpisode.season_number == season_number)
        query_params['airedSeason'] = season_number
        ep_description = '%s s%s' % (ep_description, season_number)

    if episode_number:
        episode = episode.filter(TVDBEpisode.episode_number == episode_number)
        query_params['airedEpisode'] = episode_number
        ep_description = '%s e%s' % (ep_description, episode_number)

    episode = episode.first()

    if episode:
        if episode.expired and not only_cached:
            log.info('Data for %r has expired, refreshing from tvdb', episode)
            try:
                episode.update()
            except LookupError as e:
                log.warning('Error while updating from tvdb (%s), using cached data.' % str(e))
        else:
            log.debug('Using episode info for %s from cache.', ep_description)
    else:
        if only_cached:
            raise LookupError('Episode %s not found from cache' % ep_description)
        # There was no episode found in the cache, do a lookup from tvdb
        log.debug('Episode %s not found in cache, looking up from tvdb.', ep_description)
        try:
            results = TVDBRequest().get('series/%s/episodes/query' % series.id, **query_params)
            if results:
                # Check if this episode id is already in our db
                episode = session.query(TVDBEpisode).filter(TVDBEpisode.id == results[0]['id']).first()
                if not episode:
                    episode = TVDBEpisode(id=results[0]['id'])
                if episode.expired is not False:
                    episode.update()

                series.episodes.append(episode)
                session.merge(series)
        except requests.RequestException as e:
            raise LookupError('Error looking up episode from TVDb (%s)' % e)
    if episode:
        return episode
    else:
        raise LookupError('No results found for %s' % ep_description)


@with_session
def mark_expired(session=None):
    """Marks series and episodes that have expired since we cached them"""
    # Only get the expired list every hour
    last_check = persist.get('last_check')

    if not last_check:
        persist['last_check'] = datetime.utcnow()
        return
    if datetime.utcnow() - last_check <= timedelta(hours=2):
        # It has been less than 2 hour, don't check again
        return

    new_last_check = datetime.utcnow()

    try:
        # Calculate seconds since epoch minus a minute for buffer
        last_check_epoch = int((last_check - datetime(1970, 1, 1)).total_seconds()) - 60
        log.debug("Getting updates from thetvdb (%s)" % last_check_epoch)
        updates = TVDBRequest().get('updated/query', fromTime=last_check_epoch)
    except requests.RequestException as e:
        log.error('Could not get update information from tvdb: %s', e)
        return

    def chunked(seq):
        """Helper to divide our expired lists into sizes sqlite can handle in a query. (<1000)"""
        for i in range(0, len(seq), 900):
            yield seq[i:i + 900]

    expired_series = [series['id'] for series in updates] if updates else []

    # Update our cache to mark the items that have expired
    for chunk in chunked(expired_series):
        series_updated = session.query(TVDBSeries).filter(TVDBSeries.id.in_(chunk)).update({'expired': True}, 'fetch')
        episodes_updated = session.query(TVDBEpisode).filter(TVDBEpisode.series_id.in_(chunk)).update({'expired': True}, 'fetch')
        log.debug('%s series and %s episodes marked as expired', series_updated, episodes_updated)

    persist['last_check'] = new_last_check
