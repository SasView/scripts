import json
import os
import re
import sys
import time
from datetime import datetime
from itertools import chain
from pprint import pprint
from time import mktime
from urllib.parse import urljoin, urlsplit, urlunsplit
from xmlrpc.client import MultiCall, ServerProxy

from dotenv import load_dotenv
from github import Github, GithubObject

'''
Adapted from:
https://github.com/robertoschwald/migrate-trac-issues-to-github/blob/master/migrate.py

'''

DEFAULT_GITHUB_USERNAME = "sasview-bot"

# Usernames map :: trac:github
USERNAME_MAP = {
    'Adamo': 'marcoadamo1',
    'Laura Forster': 'LauraamForster',
    'Peter Parker': 'PeterParker',
    'Tobias Richter': 'zjttoefs',
    'ajj': 'ajj',
    'andyfaff': 'andyfaff',
    'awashington': 'rprospero',
    'butler': 'butlerpd',
    'davidm': DEFAULT_GITHUB_USERNAME,  # No github
    'dirk': 'dehoni',
    'french': 'robertdfrench',
    'gonzalezm': 'gonzalezma',
    'gregsuczewski': '',
    'grethevj': 'grethevj',
    'ibressler': 'ibressler',
    'jhbakker': 'jhbakker',
    'krzywon': 'krzywon',
    'lewis': 'lewisodriscoll',
    'mathieu': 'mdoucet',
    'nat12bbo': 'nat12bbo',
    'none': 'butlerpd',  # This was Paul
    'piotr': 'rozyczko',
    'pkienzle': 'pkienzle',
    'rachelrford': 'rachelrford',
    'ricardo': 'ricleal',
    'richardh': 'RichardHeenan',
    'smk78': 'smk78',
    'srparnell': 'srparnell',
    'sylvain': 'sylvainprevost',
    'tcbennun': 'tcbennun',
    'tim': 'timsnow',
    'toqduj': DEFAULT_GITHUB_USERNAME,
    'trnielsen': 'trnielsen',
    'wimbouwman': 'wimbouwman',
    'wojciech': 'wpotrzebowski',
    'yunliu': 'yunliu01',
    '': DEFAULT_GITHUB_USERNAME,
}


def timeit(method):
    ''' Auxiliary decorator to time a function '''

    def timed(*args, **kw):
        ts = time.time()
        result = method(*args, **kw)
        te = time.time()
        if 'log_time' in kw:
            name = kw.get('log_name', method.__name__.upper())
            kw['log_time'][name] = int((te - ts) * 1000)
        else:
            print('<{}>  {:.3} s'.format(method.__name__, te - ts))
        return result
    return timed


def remove_credentials_from_url(url):
    scheme, netloc, path, query, fragment = urlsplit(url)

    if '@' in netloc:
        # Strip HTTP basic authentication from netloc:
        netloc = netloc.rsplit('@', 1)[1]

    return urlunsplit((scheme, netloc, path, query, fragment))


def convert_value_for_json(obj):
    """Converts all date-like objects into ISO 8601 formatted strings for JSON"""

    if hasattr(obj, 'timetuple'):
        return datetime.fromtimestamp(mktime(obj.timetuple())).isoformat()
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        return obj


def make_blockquote(text):
    return re.sub(r'^', '> ', text, flags=re.MULTILINE)


class Migrator(object):

    def __init__(self, *args, **kwargs):

        # load .env file
        load_dotenv()

        # TRAC
        trac_url = os.getenv("TRAC-URL")
        self.trac_public_url = remove_credentials_from_url(trac_url)
        trac_api_url = urljoin(trac_url, "/login/rpc")
        self.trac = ServerProxy(trac_api_url)
        # GITHUB
        self.GITHUB_TOKENS = eval(os.getenv("GITHUB-TOKENS"))
        #
        self.trac_issue_map = {}

    def _github_authentication(self, github_username):
        ''' authenticate github based on token given a user name
        if the username does not exist use sasview-bot
        '''

        if github_username not in self.GITHUB_TOKENS:
            github_username = DEFAULT_GITHUB_USERNAME
        token = self.GITHUB_TOKENS[github_username]
        github = Github(token)
        github_org = github.get_organization(os.getenv("GITHUB-ORGANISATION"))
        github_repo = github_org.get_repo(os.getenv("GITHUB-REPO"))
        return github_repo, github_org

    def convert_ticket_id(self, trac_id):
        trac_id = int(trac_id)
        if trac_id in self.trac_issue_map:
            return "#%s" % self.trac_issue_map[trac_id].number
        else:
            return urljoin(self.trac_public_url, '/ticket/%d' % trac_id)

    def fix_wiki_syntax(self, markup):
        '''
        Convert wiki syntax to markdown
        '''
        markup = re.sub(r'(?:refs #?|#)(\d+)', lambda i: self.convert_ticket_id(i.group(1)),
                        markup)
        markup = re.sub(r'#!CommitTicketReference.*rev=([^\s]+)\n', lambda i: i.group(1),
                        markup, flags=re.MULTILINE)

        markup = markup.replace("{{{\n", "\n```text\n")
        markup = markup.replace("{{{", "```")
        markup = markup.replace("}}}", "```")

        markup = markup.replace("[[BR]]", "\n")

        markup = re.sub(r'\[changeset:"([^"/]+?)(?:/[^"]+)?"]',
                        r"changeset \1", markup)
        return markup

    def get_gh_milestone(self, milestone):
        self.github_repo,  self.github_org = self._github_authentication('admin')
        if milestone.strip():
            if milestone not in self.gh_milestones:
                print('-> Creating Milestone: {}'.format(milestone))
                m = self.github_repo.create_milestone(milestone)
                self.gh_milestones[m.title] = m
                print('-> Created Milestone: {}'.format(m))
            return self.gh_milestones[milestone]
        else:
            return GithubObject.NotSet

    def get_gh_label(self, label):
        ''' Get the label from github. If it doesn't exist create it '''
        self.github_repo,  self.github_org = self._github_authentication('admin')
        if label not in self.gh_labels:
            print('-> Creating Label: {}'.format(label))
            self.gh_labels[label] = self.github_repo.create_label(
                label, color='FFFFFF')
            print('-> Created Label: {}'.format(self.gh_labels[label]))
        return self.gh_labels[label]

    def print_github_team_members(self):
        ''' aux function '''
        self.github_repo,  self.github_org = self._github_authentication(
            'admin')
        teams = self.github_org.get_teams()
        team = [t for t in teams if t.name ==
                'Developers'][0]  # assumes a match
        for m in team.get_members():
            print(m.login, m.email)

    def print_trac_rpc_methods(self):
        
        for method in self.trac.system.listMethods():
            print(method)
            print('\n'.join(['  ' + x for x in self.trac.system.methodHelp(
                             method).split('\n')]))
            print()
            print()

    def get_github_username(self, trac_username):
        ''' Check if trac_username exists in the dic USERNAME_MAP and returns
        it. If it doesn't exist return DEFAULT_GITHUB_USERNAME '''

        trac_username = trac_username.strip()
        if trac_username in USERNAME_MAP:
            return USERNAME_MAP[trac_username]
        else:
            print("!! Trac username '{0}' does not exist in USERNAME_MAP. "
                  "Returning {1}.".format(trac_username, DEFAULT_GITHUB_USERNAME))
            return DEFAULT_GITHUB_USERNAME

    def run(self):
        self.load_github()
        self.migrate_tickets()

    @timeit
    def load_github(self):
        '''
        Create self.gh_* dictinaries indexed by title and with GH returns as values
        '''
        print("Loading information from Github...")
        self.github_repo,  self.github_org = self._github_authentication(
            'admin')
        repo = self.github_repo
        self.gh_milestones = {
            i.title: i for i in repo.get_milestones(state="all")}
        self.gh_labels = {i.name: i for i in repo.get_labels()}
        self.gh_issues = {i.title: i for i in repo.get_issues(state="all")}

        print("Read from GitHub: {} milestones, {} labels, {} issues.".format(
            len(self.gh_milestones), len(self.gh_labels), len(self.gh_issues)
        ))

    def migrate_tickets(self):
        print("Loading information from Trac...")

        get_all_tickets = MultiCall(self.trac)

        for ticket in self.trac.ticket.query("max=0&order=id"):
            get_all_tickets.ticket.get(ticket)

        # Take the memory hit so we can rewrite ticket references:
        all_trac_tickets = list(get_all_tickets())
        trac_issue_map = {}

        print("Tickets loaded {}.".format(len(all_trac_tickets)))
        print("-"*80)
        print("Creating GitHub tickets now...")

        # TODO: Remove this
        #all_trac_tickets = all_trac_tickets[-4:-2]

        self.creat_incomplete_github_issues(all_trac_tickets, trac_issue_map)

        print("-"*80)
        print("Migrating descriptions and comments...")

        self.complete_github_issues(all_trac_tickets, trac_issue_map)

    @timeit
    def complete_github_issues(self, all_trac_tickets, trac_issue_map):
        incomplete_label = self.get_gh_label('Incomplete Migration')

        for trac_id, time_created, time_changed, attributes in all_trac_tickets:
            # TODO: Remove this
            if trac_id not in range(240,247):
                continue
            gh_issue = trac_issue_map[int(trac_id)]

            if incomplete_label.url not in [i.url for i in gh_issue.labels]:
                print("!! Issue #{:04} \tExists remotely without '{}' label. Skipping it!".format(
                    gh_issue.number, incomplete_label.name))
                continue

            gh_issue.remove_from_labels(incomplete_label)

            print("-- Issue #{:04d} \tAdding body and comments remotely: {}".format(
                gh_issue.number, gh_issue.title))

            gh_issue.edit(body="%s\n\n%s" % (self.fix_wiki_syntax(
                attributes['description']), gh_issue.body))

            changelog = self.trac.ticket.changeLog(trac_id)

            comments = {}

            for time, author, field, old_value, new_value, permanent in changelog:
                if field == 'comment':
                    if not new_value:
                        continue
                    body = '**%s** commented:\n\n%s\n\n' % (
                        author, make_blockquote(self.fix_wiki_syntax(new_value)))
                else:
                    if "\n" in old_value or "\n" in new_value:
                        body = '**%s** changed %s from:\n\n%s\n\nto:\n\n%s\n\n' % (
                            author, field, make_blockquote(old_value),
                            make_blockquote(new_value))
                    else:
                        body = '**%s** changed %s from "%s" to "%s"' % (
                            author, field, old_value, new_value)

                converted_time = datetime.strptime(
                    time.value, "%Y%m%dT%H:%M:%S")
                comments.setdefault(
                    (converted_time.strftime("%Y/%m/%d %H:%M:%S"), author), []).append(body)

            for (time, author), values in sorted(comments.items()):
                if len(values) > 1:
                    fmt = "\n* %s" % "\n* ".join(values)
                else:
                    fmt = "".join(values)

                github_repo, _ = self._github_authentication(self.get_github_username(author))

                gh_issue_permissions = github_repo.get_issue(gh_issue.number)
                gh_issue_permissions.create_comment(
                    "Trac update at `%s`: %s" % (time, fmt))

            if attributes['status'] == "closed":
                gh_issue.edit(state="closed")
            print("Issue Done!")

    @timeit
    def creat_incomplete_github_issues(self, all_trac_tickets, trac_issue_map):
        for trac_id, time_created, time_changed, attributes in all_trac_tickets:

            #TODO: REMOVE
            if trac_id not in range(240,247):
                continue

            title = "%s (Trac #%d)" % (attributes['summary'], trac_id)

            # Intentionally do not migrate description at this point so we can rewrite
            # ticket ID references after all tickets have been created in the second pass below:
            body = "Migrated from %s\n" % urljoin(
                self.trac_public_url, "/ticket/%d" % trac_id)
            text_attributes = {k: convert_value_for_json(
                v) for k, v in attributes.items()}
            body += "```json\n" + \
                json.dumps(text_attributes, indent=4) + "\n```\n"

            milestone = self.get_gh_milestone(attributes['milestone'])

            assignee = self.get_github_username(attributes['owner'])

            labels = ['Migrated from Trac', 'Incomplete Migration']

            # User does not exist in GitHub -> Add username as label
            # if (assignee == DEFAULT_GITHUB_USERNAME and (attributes['owner'] and \
            #         attributes['owner'].strip())):
            #     print("{} does not exist in GitHub -> Add username as label".format(
            #         attributes['owner']
            #     ))
            #     labels.extend([attributes['owner']])

            labels.extend(
                filter(None, (attributes['type'], attributes['component'])))
            labels = list(map(self.get_gh_label, labels))

            # Let's find our issue and assign it
            for i, j in self.gh_issues.items():
                if i == title:
                    gh_issue = j
                    if (assignee is not GithubObject.NotSet and
                        (not gh_issue.assignee
                         or (gh_issue.assignee.login != assignee))):
                        gh_issue.edit(assignee=assignee)
                    break
            else:
                # otherwise creates the issue

                github_repo, _ = self._github_authentication(
                    self.get_github_username(attributes['reporter']))

                gh_issue = github_repo.create_issue(title, assignee=assignee, body=body,
                                                    milestone=milestone, labels=labels)
                self.gh_issues[title] = gh_issue
                print("** Issue #{:04d} \tCreated issue remotely: {}\n\tWith labels: {}\n"
                      "\tLabels created remotely: {}".format(
                          gh_issue.number, title, sorted([l.name for l in labels]), 
                          sorted([l.name for l in gh_issue.labels])))

            trac_issue_map[int(trac_id)] = gh_issue
        

if __name__ == "__main__":
    m = Migrator()
    m.run()
