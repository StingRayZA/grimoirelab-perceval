# -*- coding: utf-8 -*-
#
# Copyright (C) 2016-2017 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, 51 Franklin Street, Fifth Floor, Boston, MA 02110-1335, USA.
#
# Authors:
#     Alberto Martín <alberto.martin@bitergia.com>
#     Santiago Dueñas <sduenas@bitergia.com>
#

import json
import logging
import time

from grimoirelab.toolkit.datetime import datetime_to_utc
from grimoirelab.toolkit.uris import urijoin

from ...backend import (Backend,
                        BackendCommand,
                        BackendCommandArgumentParser)
from ...client import HttpClient
from ...utils import DEFAULT_DATETIME

CATEGORY_QUESTION = "question"

MAX_QUESTIONS = 100  # Maximum number of reviews per query

logger = logging.getLogger(__name__)


class StackExchange(Backend):
    """StackExchange backend for Perceval.

    This class retrieves the questions stored in any of the
    StackExchange sites. To initialize this class the
    site must be provided.

    :param site: StackExchange site
    :param tagged: filter items by question Tag
    :param api_token: StackExchange access_token for the API
    :param max_questions: max of questions per page retrieved
    :param tag: label used to mark the data
    :param archive: archive to store/retrieve items
    """
    version = '0.10.3'

    CATEGORIES = [CATEGORY_QUESTION]

    def __init__(self, site, tagged=None, api_token=None,
                 max_questions=MAX_QUESTIONS, tag=None, archive=None):
        origin = site

        super().__init__(origin, tag=tag, archive=archive)
        self.site = site
        self.api_token = api_token
        self.tagged = tagged
        self.max_questions = max_questions

        self.client = None

    def fetch(self, category=CATEGORY_QUESTION, from_date=DEFAULT_DATETIME):
        """Fetch the questions from the site.

        The method retrieves, from a StackExchange site, the
        questions updated since the given date.

        :param from_date: obtain questions updated since this date

        :returns: a generator of questions
        """
        if not from_date:
            from_date = DEFAULT_DATETIME

        from_date = datetime_to_utc(from_date)

        kwargs = {'from_date': from_date}
        items = super().fetch(category, **kwargs)

        return items

    def fetch_items(self, category, **kwargs):
        """Fetch the questions

        :param category: the category of items to fetch
        :param kwargs: backend arguments

        :returns: a generator of items
        """
        from_date = kwargs['from_date']

        logger.info("Looking for questions at site '%s', with tag '%s' and updated from '%s'",
                    self.site, self.tagged, str(from_date))

        whole_pages = self.client.get_questions(from_date)

        for whole_page in whole_pages:
            questions = self.parse_questions(whole_page)
            for question in questions:
                yield question

    @classmethod
    def has_archiving(cls):
        """Returns whether it supports archiving items on the fetch process.

        :returns: this backend supports items archive
        """
        return True

    @classmethod
    def has_resuming(cls):
        """Returns whether it supports to resume the fetch process.

        :returns: this backend supports items resuming
        """
        return True

    @staticmethod
    def metadata_id(item):
        """Extracts the identifier from a StackExchange item."""

        return str(item['question_id'])

    @staticmethod
    def metadata_updated_on(item):
        """Extracts the update time from a StackExchange item.

        The timestamp is extracted from 'last_activity_date' field.
        This date is a UNIX timestamp but needs to be converted to
        a float value.

        :param item: item generated by the backend

        :returns: a UNIX timestamp
        """
        return float(item['last_activity_date'])

    @staticmethod
    def metadata_category(item):
        """Extracts the category from a StackExchange item.

        This backend only generates one type of item which is
        'question'.
        """
        return CATEGORY_QUESTION

    @staticmethod
    def parse_questions(raw_page):
        """Parse a StackExchange API raw response.

        The method parses the API response retrieving the
        questions from the received items

        :param items: items from where to parse the questions

        :returns: a generator of questions
        """
        raw_questions = json.loads(raw_page)
        questions = raw_questions['items']
        for question in questions:
            yield question

    def _init_client(self, from_archive=False):
        """Init client"""

        return StackExchangeClient(self.site, self.tagged, self.api_token, self.max_questions,
                                   self.archive, from_archive)


class StackExchangeClient(HttpClient):
    """StackExchange API client.

    This class implements a simple client to retrieve questions from
    any Stackexchange site.

    :param site: URL of the Bugzilla server
    :param tagged: filter items by question Tag
    :param token: StackExchange access_token for the API
    :param max_questions: max number of questions per query
    :param archive: an archive to store/read fetched data
    :param from_archive: it tells whether to write/read the archive

    :raises HTTPError: when an error occurs doing the request
    """
    # Filters are immutable and non-expiring. This filter allows to retrieve all
    # the information regarding Each question. To know more, visit
    # https://api.stackexchange.com/docs/questions and paste the filter in the
    # whitebox filter. It will display a list of checkboxes with the selected
    # values for the filter provided.

    QUESTIONS_FILTER = 'Bf*y*ByQD_upZqozgU6lXL_62USGOoV3)MFNgiHqHpmO_Y-jHR'
    STACKEXCHANGE_API_URL = 'https://api.stackexchange.com'
    VERSION_API = '2.2'

    def __init__(self, site, tagged, token, max_questions=MAX_QUESTIONS, archive=None, from_archive=False):
        super().__init__(self.STACKEXCHANGE_API_URL, archive=archive, from_archive=from_archive)
        self.site = site
        self.tagged = tagged
        self.token = token
        self.max_questions = max_questions

    def __build_payload(self, page, from_date, order='desc', sort='activity'):
        payload = {'page': page,
                   'pagesize': self.max_questions,
                   'order': order,
                   'sort': sort,
                   'tagged': self.tagged,
                   'site': self.site,
                   'key': self.token,
                   'filter': self.QUESTIONS_FILTER}
        if from_date:
            timestamp = int(from_date.timestamp())
            payload['min'] = timestamp
        return payload

    def __log_status(self, quota_remaining, quota_max, page_size, total):

        logger.debug("Rate limit: %s/%s" % (quota_remaining,
                                            quota_max))
        if (total != 0):
            nquestions = min(page_size, total)
            logger.info("Fetching questions: %s/%s" % (nquestions,
                                                       total))
        else:
            logger.info("No questions were found.")

    def get_questions(self, from_date):
        """Retrieve all the questions from a given date.

        :param from_date: obtain questions updated since this date
        """

        page = 1
        url = urijoin(self.base_url, self.VERSION_API, "questions")

        req = self.fetch(url, payload=self.__build_payload(page, from_date))
        questions = req.text

        data = req.json()
        tquestions = data['total']
        nquestions = data['page_size']

        self.__log_status(data['quota_remaining'],
                          data['quota_max'],
                          nquestions,
                          tquestions)

        while questions:
            yield questions
            questions = None

            if data['has_more']:
                page += 1

                backoff = data.get('backoff', None)
                if backoff:
                    logger.debug("Expensive query. Wait %s secs to send a new request",
                                 backoff)
                    time.sleep(float(backoff))

                req = self.fetch(url, payload=self.__build_payload(page, from_date))
                data = req.json()
                questions = req.text
                nquestions += data['page_size']
                self.__log_status(data['quota_remaining'],
                                  data['quota_max'],
                                  nquestions,
                                  tquestions)


class StackExchangeCommand(BackendCommand):
    """Class to run StackExchange backend from the command line."""

    BACKEND = StackExchange

    @staticmethod
    def setup_cmd_parser():
        """Returns the StackExchange argument parser."""

        parser = BackendCommandArgumentParser(from_date=True,
                                              token_auth=True,
                                              archive=True)

        # StackExchange options
        group = parser.parser.add_argument_group('StackExchange arguments')
        group.add_argument('--site', dest='site',
                           required=True,
                           help="StackExchange site")
        group.add_argument('--tagged', dest='tagged',
                           help="filter items by question Tag")
        group.add_argument('--max-questions', dest='max_questions',
                           type=int, default=MAX_QUESTIONS,
                           help="Maximum number of questions requested in the same query")

        return parser
