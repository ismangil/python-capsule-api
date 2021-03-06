import requests
import requests.auth
from decimal import Decimal
import datetime
import json
from collections import OrderedDict


def capsule_datetime_to_utc_aware(datetime_string):
    return datetime.datetime.strptime(datetime_string, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)


class CustomFieldsMixin(object):
    @property
    def customfields(self):
        def to_tuple(entry):
            if 'text' in entry:
                return (entry['label'], entry['text'])
            if 'boolean' in entry:
                return (entry['label'], entry['boolean'] == 'true')
            if 'number' in entry:
                return (entry['label'], entry['number'])
            raise ValueError

        try:
            custom_fields = self.get('raw_customfields') or self['customfields']  # FIXME attempts old format until all objects are converted to raw_
            return dict(to_tuple(x) for x in custom_fields)
        except KeyError:
            raise AttributeError('customfields')

    @property
    def datatags(self):
        try:
            return OrderedDict((x.get('tag') or x['label'], capsule_datetime_to_utc_aware(x['date']).date()) for x in sorted(self['raw_datatags'], key=lambda x: x['date']))
        except KeyError:
            raise AttributeError('datatags')

    @property
    def tags(self):
        return list(x['name'] for x in self['tags_id'])

    def load_customfields_from_api(self, customfields):
        self['raw_customfields'] = [x for x in customfields if 'date' not in x]
        self['raw_datatags'] = [x for x in customfields if 'date' in x]

    def load_tags_from_api(self, tags):
        self['tags_id'] = [x for x in tags]


class Opportunity(dict, CustomFieldsMixin):

    @property
    def createdOn(self):
        return capsule_datetime_to_utc_aware(self['createdOn'])

    @property
    def expectedCloseDate(self):
        try:
            return capsule_datetime_to_utc_aware(self['expectedCloseDate'])
        except KeyError:
            raise AttributeError

    @property
    def actualCloseDate(self):
        try:
            return capsule_datetime_to_utc_aware(self['actualCloseDate'])
        except KeyError:
            raise AttributeError

    @property
    def updatedOn(self):
        return capsule_datetime_to_utc_aware(self['updatedOn'])

    @property
    def open(self):
        return 'actualCloseDate' not in self

    @property
    def probability(self):
        return int(self['probability'])

    @property
    def milestoneId(self):
        return int(self['milestoneId'])

    @property
    def value(self):
        try:
            return Decimal(self['value'])
        except KeyError:
            return Decimal(0)

    @property
    def weighted_value(self):
        return self.value * self.probability / 100

    @property
    def positive_outcome(self):
        return not self.open and self.probability == 100

    @property
    def negative_outcome(self):
        return not self.open and self.probability == 0

    def load_tasks_from_api(self, tasks):
        if any(x for x in tasks if x.get('opportunityId') != self.id):
            raise Exception
        self['raw_tasks'] = tasks

    def __getattr__(self, element):
        if element == 'customfields':
            raise AttributeError
        if element in self:
            return self[element]
        if element in self.customfields:
            return self.customfields[element]
        raise AttributeError


class Phone(dict):

    @property
    def id(self):
        return self['id']

    @property
    def phone_number(self):
        return self['phoneNumber']

    def __str__(self):
        return self.phone_number


class Email(dict):

    @property
    def id(self):
        return self['id']

    @property
    def email_address(self):
        return self['emailAddress']

    def __str__(self):
        return self.email_address


class Party(dict, CustomFieldsMixin):
    Phone = Phone
    Email = Email

    @property
    def id(self):
        return self['id']

    @property
    def name(self):
        raise NotImplementedError('name')

    @property
    def about(self):
        try:
            return self['about']
        except KeyError:
            raise AttributeError('about')

    @property
    def contacts(self):
        # capsule returns an empty string if no contact details are provided
        return self['contacts'] or {}

    @property
    def emails(self):
        emails = self.contacts.get('email')
        if not emails:
            raise AttributeError('emails')
        if isinstance(emails, dict):
            emails = [emails]
        return [self.Email(e) for e in emails]

    @property
    def phone_numbers(self):
        phone_numbers = self.contacts.get('phone')
        if not phone_numbers:
            raise AttributeError('phone_numbers')
        if isinstance(phone_numbers, dict):
            phone_numbers = [phone_numbers]
        return [self.Phone(p) for p in phone_numbers]


    def __getattr__(self, element):
        if element == 'customfields':
            raise AttributeError(element)
        if element in self:
            return self[element]
        if element in self.customfields:
            return self.customfields[element]
        raise AttributeError(element)


class Person(Party):
    @property
    def first_name(self):
        try:
            return self['firstName']
        except KeyError:
            raise AttributeError('first_name')

    @property
    def last_name(self):
        try:
            return self['lastName']
        except KeyError:
            raise AttributeError('last_name')

    @property
    def name(self):
        ret = ' '.join(n for n in (
            getattr(self, 'first_name', None),
            getattr(self, 'last_name', None)
        ) if n)
        if not ret:
            raise Exception("%s: Party doesn't have first or last name." % self.id)
        return ret

    @property
    def title(self):
        try:
            return self['title']
        except KeyError:
            raise AttributeError('title')

    @property
    def job_title(self):
        try:
            return self['jobTitle']
        except KeyError:
            raise AttributeError('job_title')


class Organisation(Party):
    @property
    def name(self):
        return self['name']


class Task(dict):

    @property
    def id(self):
        return self['id']

    @property
    def description(self):
        return self['description']

    @property
    def details(self):
        return self['details']

    @property
    def owner(self):
        return self['owner']

    def __getattr__(self, element):
        try:
            return self[element]
        except KeyError:
            raise AttributeError(element)


class CapsuleAPI(object):
    Opportunity = Opportunity
    Organisation = Organisation
    Person = Person
    Task = Task

    def __init__(self, capsule_name, capsule_key):
        self.capsule_name = capsule_name
        self.capsule_key = capsule_key
        self.base_url = "https://%s.capsulecrm.com/api/" % capsule_name

    def request(self, method, path, **kwargs):
        headers = {
            'accept': 'application/json',
            'content-type': 'application/json' 
        }
        auth = requests.auth.HTTPBasicAuth(self.capsule_key, self.capsule_name)
        method = method.lower()
        if method == 'get':
            result = requests.get(self.base_url + path, headers=headers, params=kwargs, auth=auth)
            result.raise_for_status()
            return result.json()
        if method in ('put', 'post', 'delete'):
            result = getattr(requests, method)(self.base_url + path, headers=headers, data=json.dumps(kwargs), auth=auth)
            result.raise_for_status()
            return result
        else:
            raise ValueError

    def get(self, path, **kwargs):
        return self.request('get', path, **kwargs)

    def put(self, path, data):
        return self.request('put', path, **data)

    def post(self, path, data):
        return self.request('post', path, **data)

    def delete(self, path, data):
        return self.request('delete', path, **data)

    def get_opportunities_by_party(self, party):
        result = self.get('party/%d/opportunity' %int(party.id))['opportunities'].get('opportunity')
        if not result:
            return []
        if isinstance(result, dict):
            result = [result]
        return [self.Opportunity(x) for x in result]

    def opportunities(self, **kwargs):
        result = self.get('opportunity', **kwargs)['opportunities'].get('opportunity')
        if not result:
            return []
        if isinstance(result, dict):
            result = [result]
        return [self.Opportunity(x) for x in result]

    def full_opportunities(self, **kwargs):
        opportunities = self.opportunities(**kwargs)
        for opportunity in opportunities:
            self.inject_opportunity_customfields(opportunity)
            self.inject_opportunity_tags(opportunity)
        return opportunities

    def delete_opportunity(self, opportunity_id):
        return self.delete('opportunity/' + str(opportunity_id), {})

    def opportunity(self, opportunity_id):
        result = self.get('opportunity/' + str(opportunity_id))
        return self.Opportunity(result['opportunity'])

    def full_opportunity(self, opportunity_id):
        opportunity = self.opportunity(opportunity_id)
        self.inject_opportunity_customfields(opportunity)
        self.inject_opportunity_tags(opportunity)
        return opportunity

    def opportunity_customfields(self, opportunity_id, **kwargs):
        result = self.get('opportunity/' + opportunity_id + '/customfields', **kwargs)
        if not result['customFields'].get('customField'):
            return []
        customfields = result['customFields']['customField']
        if isinstance(customfields, dict):
            customfields = [customfields]
        return customfields

    def opportunity_tags(self, opportunity_id, **kwargs):
        result = self.get('opportunity/' + opportunity_id + '/tag', **kwargs)
        return result['tags'].get('tag') or []

    def inject_opportunity_customfields(self, opportunity):
        return opportunity.load_customfields_from_api(self.opportunity_customfields(opportunity.id))

    def inject_opportunity_tags(self, opportunity):
        return opportunity.load_tags_from_api(self.opportunity_tags(opportunity.id))

    def put_datatag(self, opportunity, name, date=None):
        date = date or datetime.date.today()
        new_datatag = {'tag': name, 'label': 'Date', 'date': date.strftime('%Y-%m-%dT00:00:00Z')}
        result = {'customFields': {'customField': [new_datatag]}}
        self.put('opportunity/' + opportunity.id + '/customfields', result)

    def post_organisation(self, organisation):
        data = {'organisation': organisation}
        resp = self.post('organisation', data)
        return resp.headers['location'].split('/')[-1]

    def post_person(self, person):
        if 'firstName' not in person and 'lastName' not in person:
            raise ValueError('first_name or last_name must be provided')
        data = {'person': person}
        resp = self.post('person', data)
        return resp.headers['location'].split('/')[-1]

    def put_person(self, person_id, person):
        data = {'person': person}
        self.put('person/%d' % int(person_id), data)

    def post_opportunity(self, name, party_id, milestone_id, **kwargs):
        kwargs['name'] = name
        kwargs['milestoneId'] = milestone_id
        data = {'opportunity': kwargs}
        resp = self.post('party/%d/opportunity' % int(party_id), data)
        return resp.headers['location'].split('/')[-1]

    def put_opportunity(self, opportunity_id, **kwargs):
        data = {'opportunity': kwargs}
        self.put('opportunity/%d' % int(opportunity_id), data)

    def put_opportunity_customfields(self, opportunity_id, data):
        if isinstance(data, dict):
            data = [data]
        data = {'customFields': {'customField': data}}
        self.put('opportunity/%d/customfields' % int(opportunity_id), data)

    def post_opportunity_history(self, opportunity_id, **kwargs):
        data = {'historyItem': kwargs}
        resp = self.post('opportunity/%d/history' % int(opportunity_id), data)
        return resp.headers['location'].split('/')[-1]

    def get_opportunity_history(self, opportunity_id):
        result = self.get('opportunity/%d/history' % int(opportunity_id))
        history = result.get('history', {}).get('historyItem')
        if not history:
            return []
        if isinstance(history, dict):
            history = [history]
        return history

    def get_party_history(self, party_id):
        result = self.get('party/%d/history' % int(party_id))
        history = result.get('history', {}).get('historyItem')
        if not history:
            return []
        if isinstance(history, dict):
            history = [history]
        return history

    def milestones(self):
        resp = self.get('opportunity/milestones')
        milestones = resp['milestones'].get('milestone')
        if not milestones:
            return []
        if isinstance(milestones, dict):
            milestones = [milestones]
        return milestones

    def users(self):
        resp = self.get('users')
        users = resp['users'].get('user')
        if not users:
            return []
        if isinstance(users, dict):
            users = [users]
        return users

    def parties(self, start=None, limit=None, **kwargs):
        params = {}
        if start is not None:
            params['start'] = start
        if limit is not None:
            params['limit'] = limit
        params.update(kwargs)
        result = self.get('party', **params)['parties']
        people = result.get('person')
        if not people:
            people = []
        if isinstance(people, dict):
            people = [people]

        organisations = result.get('organisation')
        if not organisations:
            organisations = []
        if isinstance(organisations, dict):
            organisations = [organisations]
        return [self.Person(x) for x in people], [self.Organisation(x) for x in organisations]

    def party(self, party_id):
        result = self.get('party/%s' % str(party_id))
        try:
            return self.Person(result['person'])
        except KeyError:
            return self.Organisation(result['organisation'])

    def full_party(self, party_id):
        party = self.party(party_id)
        self.inject_party_customfields(party)
        return party

    def full_parties(self, start=None, limit=None, **kwargs):
        people, organisations = self.parties(start=start, limit=limit, **kwargs)
        for party in people + organisations:
            self.inject_party_customfields(party)
        return people, organisations

    def parties_from_opportunity(self, opportunity_id):
        result = self.get('opportunity/%s/party' % opportunity_id)['parties']
        people = result.get('person')
        if not people:
            people = []
        if isinstance(people, dict):
            people = [people]

        organisations = result.get('organisation')
        if not organisations:
            organisations = []
        if isinstance(organisations, dict):
            organisations = [organisations]
        return [self.Person(x) for x in people], [self.Organisation(x) for x in organisations]

    def full_parties_from_opportunity(self, opportunity_id):
        people, organisations = self.parties_from_opportunity(opportunity_id)
        for party in people + organisations:
            self.inject_party_customfields(party)
        return people, organisations

    def people(self, party_id):
        result = self.get('party/%s/people' % str(party_id))['parties']
        people = result.get('person')
        if not people:
            people = []
        if isinstance(people, dict):
            people = [people]
        return [self.Person(x) for x in people]

    def full_people(self, party_id):
        people = self.people(party_id)
        for person in people:
            self.inject_party_customfields(person)
        return people

    def party_customfields(self, party_id, **kwargs):
        result = self.get('party/' + party_id + '/customfields', **kwargs)
        if not result['customFields'].get('customField'):
            return []
        customfields = result['customFields']['customField']
        if isinstance(customfields, dict):
            customfields = [customfields]
        return customfields

    def inject_party_customfields(self, party):
        return party.load_customfields_from_api(self.party_customfields(party.id))

    def task(self, task_id):
        result = self.get('task/%s' % str(task_id))
        return self.Task(result['task'])

    def tasks(self, **kwargs):
        result = self.get('tasks', **kwargs)['tasks']['task']
        if not result:
            return []
        if isinstance(result, dict):
            result = [result]
        return [self.Task(x) for x in result]

    def complete_task(self, task_id, **kwargs):
        self.post('task/%d/complete' % int(task_id), {})

    def put_task(self, task_id, **kwargs):
        data = {'task': kwargs}
        result = self.put('task/%d' % int(task_id), data).json()
        return self.Task(result['task'])

    def post_history_to_opportunity(self, opportunity_id, **kwargs):
        data = {'historyItem': kwargs}
        self.post('opportunity/%d/history' % int(opportunity_id), data)

    def add_additional_party_to_opportunity(self, opportunity_id, party_id):
        self.post('opportunity/%d/party/%d' % (int(opportunity_id), int(party_id)), {})

    def put_organisation(self, party_id, **kwargs):
        data = {'organisation': kwargs}
        result = self.put('organisation/%d' % int(party_id), data).json()
        return self.Organisation(result['organisation'])
