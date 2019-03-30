import difflib
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
# Only the queried tickets will stay at the mapped GH repo
# github repo name : trac query
GITHUB_REPO_TRAC_QUERY_MAP = {
    'sasmodel-marketplace': 'max=0&order=id&component=!SansView&component=!SasView&component=!sasmodels',
    "sasview": "max=0&order=id&component=SansView&or&component=SasView",
    'sasmodels': 'max=0&order=id&component=sasmodels',
    #'sasmodel-marketplace': 'max=0&order=id&component=sasmodels%20Markeplace',
    #"temp": "max=0&order=id&id=839",
    #"temp2": "max=0&order=id&id=1242&or&id=1243&or&id=1244",
}

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
    '''
    Removes the USERNAME:PASSWORD from a URL:
    https://USERNAME:PASSWORD@trac.sasview.org
    '''
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
    ''' Make a bloquote in MD'''
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

        # Member variables
        # repo : track ticket number : gh issue obj
        self.trac_issue_map = {repo: {} for repo in GITHUB_REPO_TRAC_QUERY_MAP}
        # Repo : milestone title : gh milestone obj
        self.gh_milestones = {repo: {} for repo in GITHUB_REPO_TRAC_QUERY_MAP}
        # Repo : label title : gh label obj
        self.gh_labels = {repo: {} for repo in GITHUB_REPO_TRAC_QUERY_MAP}
        # Repo : issue title : igh ssue obj
        self.gh_issues = {repo: {} for repo in GITHUB_REPO_TRAC_QUERY_MAP}
        # repo : gh repo object (already logged in)
        self.gh_repos = {repo: {} for repo in GITHUB_REPO_TRAC_QUERY_MAP}

    def _github_authentication(self, github_username, repo_name):
        ''' authenticate github based on token given a user name
        @returns a gh repo object authenticated
        '''
        github_username = github_username.strip()
        if github_username not in self.GITHUB_TOKENS:
            github_username = DEFAULT_GITHUB_USERNAME
        token = self.GITHUB_TOKENS[github_username]
        github = Github(token)
        github_org = github.get_organization(os.getenv("GITHUB-ORGANISATION"))
        github_repo = github_org.get_repo(repo_name)
        return github_repo

    def _get_github_username(self, trac_username):
        ''' Check if a close trac_username (difflib.get_close_matches) exists 
        in the dic USERNAME_MAP and returns it. 
        If it doesn't exist return DEFAULT_GITHUB_USERNAME '''

        trac_username = trac_username.strip()
        possible_trac_username = difflib.get_close_matches(
            trac_username, list(USERNAME_MAP.keys()))
        if len(possible_trac_username) == 1:
            trac_username = possible_trac_username[0]
        return USERNAME_MAP.get(trac_username, DEFAULT_GITHUB_USERNAME)

    def convert_ticket_id(self, trac_id, current_repo_name):
        ''' Convert reference of trac ticked id to github issue number 
        If track id is not in the map, return link to the old trac url
        If ticked id in the same repo => #n
        otherwise => Organization_name/Repository# and issue or pull request number
        '''

        def find_issue(trac_id, d):
            ''' return (repo_name, gh_issue) or None '''
            for k, v in d.items():  # Dict of repo_names : dict(track_id, gh_issue)
                for k2, v2 in v.items():  # track_id : gh_issue
                    if trac_id == k2:
                        return (k, v2)
            return None

        trac_id = int(trac_id)
        # find the ticket:
        result = find_issue(trac_id, self.trac_issue_map)
        if result is None:
            return urljoin(self.trac_public_url, '/ticket/{}'.format(trac_id))
        elif current_repo_name == result[0]:
            return "#{}".format(result[1].number)
        else:
            org_name = os.getenv("GITHUB-ORGANISATION")
            return "{}/{}#{}".format(org_name, result[0], result[1].number)

    def fix_wiki_syntax(self, markup, repo_name):
        '''
        Convert wiki syntax to markdown
        '''
        markup = re.sub(r'(?:refs #?|#)(\d+)', lambda i: self.convert_ticket_id(i.group(1), repo_name),
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

    def get_gh_repo(self, github_username, repo_name):
        ''' Get the authenticated repo for github_username '''
        if github_username not in self.gh_repos[repo_name]:
            self.gh_repos[repo_name][github_username] = self._github_authentication(
                github_username, repo_name)
        return self.gh_repos[repo_name][github_username]

    def get_gh_milestone(self, milestone, repo_name):
        '''
        @param milestone : milestone name
        Creates a milestone in github if it does'nt exist
        @return milestone object
        '''
        if milestone.strip():
            if milestone not in self.gh_milestones[repo_name]:
                print('-> Creating Milestone: {}'.format(milestone))
                repo = self.get_gh_repo(DEFAULT_GITHUB_USERNAME, repo_name)
                m = repo.create_milestone(milestone)
                self.gh_milestones[repo_name][m.title] = m
            return self.gh_milestones[repo_name][milestone]
        else:
            return GithubObject.NotSet

    def get_gh_label(self, label, repo_name):
        ''' Get the label from github. If it doesn't exist create it '''
        if label not in self.gh_labels[repo_name]:
            print('-> Creating Label: {}'.format(label))
            repo = self.get_gh_repo(DEFAULT_GITHUB_USERNAME, repo_name)
            self.gh_labels[repo_name][label] = repo.create_label(
                label, color='FFFFFF')
        return self.gh_labels[repo_name][label]

    def run(self):
        ''' Main cycle: iterates over all repos names and the respective queries '''
        
        self.load_github()
        self.migrate_tickets()

    @timeit
    def load_github(self):
        '''
        Create self.gh_* dictinaries indexed by title and with GH objects
        Creates issues 
        '''
        for repo_name, query in GITHUB_REPO_TRAC_QUERY_MAP.items():
            print("Loading existing information on Github repo {}...".format(repo_name))
            repo = self._github_authentication(DEFAULT_GITHUB_USERNAME, repo_name)
            self.gh_milestones[repo_name] = {
                i.title: i for i in repo.get_milestones(state="all")}
            self.gh_labels[repo_name] = {i.name: i for i in repo.get_labels()}
            self.gh_issues[repo_name] = {
                i.title: i for i in repo.get_issues(state="all")}

            print("Read from GitHub Repo {}: {} milestones, {} labels, {} issues.".format(
                repo_name, len(self.gh_milestones[repo_name]), len(
                    self.gh_labels[repo_name]), len(self.gh_issues[repo_name])
            ))

    def migrate_tickets(self):
        '''
        Executes the trac query and calls the functions to initiate and terminate 
        the GH migration
        '''
        for repo_name, query in GITHUB_REPO_TRAC_QUERY_MAP.items():
            print("Loading information from Trac query '{}' and migrating it to repo '{}'...".format(
                query, repo_name))

            get_all_tickets = MultiCall(self.trac)
            for ticket in self.trac.ticket.query(query):
                get_all_tickets.ticket.get(ticket)

            # Take the memory hit so we can rewrite ticket references:
            all_trac_tickets = list(get_all_tickets())
            print("Tickets loaded {}.".format(len(all_trac_tickets)))

            print("-"*80)
            print("Creating GitHub tickets now...")
            self.creat_incomplete_github_issues(all_trac_tickets, repo_name)

            print("-"*80)
            print("Migrating descriptions and comments...")
            self.complete_github_issues(all_trac_tickets, repo_name)

    @timeit
    def creat_incomplete_github_issues(self, all_trac_tickets, repo_name):
        '''
        Creates GH labels, milestones and tickets with label 'Incomplete Migration'
        '''
        for trac_id, time_created, time_changed, attributes in all_trac_tickets:

            title = "%s (Trac #%d)" % (attributes['summary'], trac_id)

            # Intentionally do not migrate description at this point so we can rewrite
            # ticket ID references after all tickets have been created in the second pass below:
            body = "Migrated from %s\n" % urljoin(
                self.trac_public_url, "/ticket/%d" % trac_id)
            text_attributes = {k: convert_value_for_json(
                v) for k, v in attributes.items()}
            body += "```json\n" + \
                json.dumps(text_attributes, indent=4) + "\n```\n"

            milestone = self.get_gh_milestone(attributes['milestone'], repo_name)

            assignee = '' if attributes['owner'].strip() == '' \
                else self._get_github_username(attributes['owner'])
            # The reporter must have gh repo write permissions to assign issues and labels!!!
            reporter = self._get_github_username(attributes['reporter'])

            print("## Trac  #{:04d} \tAssignee GH: {} Reporter GH: {}.".format(
                trac_id, assignee, reporter
            ))

            labels = ['Migrated from Trac', 'Incomplete Migration', attributes.get('type', None), 
                       attributes.get('workpackage', None), attributes.get('priority', None)]
            labels = list(filter(None, labels))
            labels = list(map(lambda label: self.get_gh_label(label, repo_name), labels))

            # labels = list(map(self.get_gh_label, labels))

            # Let's find if our issue exists in the dic self.gh_issues.
            for i, j in self.gh_issues[repo_name].items():
                if i == title:
                    gh_issue = j
                    print("** Issue #{:04d} \tExists already in gh_issues!".format(
                        gh_issue.number))
                    # if the issue in the dic does not have an assignee or the assignee is diferent
                    if not gh_issue.assignee or gh_issue.assignee.login != assignee:
                        gh_issue.edit(assignee=assignee)
                    break
            else:
                # If issue not found creates the issue
                #github_repo, _ = self._github_authentication(reporter)
                github_repo = self.get_gh_repo(reporter, repo_name)
                gh_issue = github_repo.create_issue(title, assignee=assignee, body=body,
                                                    milestone=milestone, labels=labels)
                self.gh_issues[repo_name][title] = gh_issue
                print("** Issue #{:04d} \tCreated issue remotely: {}. "
                      "\n\tWith Assignee: {}.\n\tWith labels: {}.\n"
                      "\tLabels created remotely: {}.".format(
                          gh_issue.number, title, assignee,
                          sorted([l.name for l in labels]),
                          sorted([l.name for l in gh_issue.labels])))

            self.trac_issue_map[repo_name][int(trac_id)] = gh_issue

    @timeit
    def complete_github_issues(self, all_trac_tickets, repo_name):
        ''' Removes 'Incomplete Migration' from GH issues and update MD body
        and adds the comments '''

        incomplete_label = self.get_gh_label('Incomplete Migration', repo_name)

        for trac_id, time_created, time_changed, attributes in all_trac_tickets:

            gh_issue = self.trac_issue_map[repo_name][int(trac_id)]

            if incomplete_label.url not in [i.url for i in gh_issue.labels]:
                print("!! Issue #{:04} \tExists remotely without '{}' label. Skipping it!".format(
                    gh_issue.number, incomplete_label.name))
                continue

            gh_issue.remove_from_labels(incomplete_label)

            print("-- Issue #{:04d} \tAdding body and comments remotely: {}".format(
                gh_issue.number, gh_issue.title))

            gh_issue.edit(body="{}\n\n{}".format(
                self.fix_wiki_syntax(attributes['description'], repo_name), gh_issue.body))

            changelog = self.trac.ticket.changeLog(trac_id)

            comments = {}

            for time, author, field, old_value, new_value, permanent in changelog:
                if field == 'comment':
                    if not new_value:
                        continue
                    body = '**%s** commented:\n\n%s\n\n' % (
                        author, self.fix_wiki_syntax(new_value, repo_name))
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

                #github_repo, _ = self._github_authentication(self._get_github_username(author))
                github_repo = self.get_gh_repo(self._get_github_username(author), repo_name)

                gh_issue_permissions = github_repo.get_issue(gh_issue.number)
                gh_issue_permissions.create_comment(
                    "Trac update at `%s`: %s" % (time, fmt))

            if attributes['status'] == "closed":
                gh_issue.edit(state="closed")
            print("\tIssue Done!")

    def print_trac_rpc_methods(self):

        for method in self.trac.system.listMethods():
            print(method)
            print('\n'.join(['  ' + x for x in self.trac.system.methodHelp(
                             method).split('\n')]))
            print()
            print()


if __name__ == "__main__":
    m = Migrator()
    m.run()
