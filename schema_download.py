"""
Copyright (c) 2011, Anthony Garcia <lagg@lavabit.com>

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""

import json, os, sys, subprocess, time, logging, urllib2
import threading, Queue

# Configuration

api_key = None
language = "en_US"
fetch_timeout = 5
games = {"Portal 2": 620,
         "Team Fortress 2": 440,
         "Team Fortress 2 Beta": 520,
         "DOTA 2": 570,
         "DOTA 2 Alt 1": 816,
         "DOTA 2 Beta": 205790,
         "Counter Strike Global Offensive": 730
         }

# GIT_WORKING_TREE is where the files are
tracker_dir = os.path.join(os.getcwd(), "schema-tracking/")

# GIT_DIR is the location of the actual git repository
tracker_git_dir = os.path.join(tracker_dir, ".git")

# URL to push to, set to None or empty if you don't want this
tracker_push_url = os.path.join(os.getcwd(), "schema-tracking-bare.git")

# Change to the location of your git install's binary (probably not needed)
git_binary = "/usr/bin/git"

# Name to commit with
git_name = "TF Wiki"
# Email (will show up in log, set this to something non-existent unless you like spam)
git_email = "noreply@wiki.teamfortress.com"

# Number of seconds to sleep between schema checks, can be less than 1 (e.g. 0.50 for half a second)
schema_check_interval = 10

# Max number of concurrent connections to allow.
connection_pool_size = len(games)

# User agent to send in HTTP requests
connection_user_agent = "Lagg/Wiki-Tracker"

# End configuration

# Different handlers can be set for this, maybe for log display in IRC at some point
log = logging.getLogger("schema-daemon")
log.setLevel(logging.INFO)

log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(logging.Formatter("%(levelname)s:\t %(message)s"))

log.addHandler(log_handler)

# Keeps track of last-modified stamps for both schemas
api_lm_store = {}
client_lm_store = {}

# Caches items_game URLs
client_schema_urls = {}

bitbucket = open(os.devnull, "w")

git_env = {"GIT_DIR": tracker_git_dir, "GIT_WORKING_TREE": tracker_dir,
           "GIT_AUTHOR_EMAIL": git_email, "GIT_AUTHOR_NAME": git_name,
           "GIT_COMMITTER_EMAIL": git_email, "GIT_COMMITTER_NAME": git_name}

def run_git(command, *args):
    # Might want to do something about this later with better logging, but right now it's just going to be spam
    code = subprocess.Popen([git_binary, command] + list(args), env = git_env, cwd = tracker_dir, stdout = bitbucket, stderr = bitbucket).wait()
    log.info("Running git {0} ({1})".format(command, code))
    return code

if not os.path.exists(tracker_dir):
    print("Initializing " + tracker_dir)
    ret = subprocess.Popen([git_binary, "init", tracker_dir]).wait()
    if ret != 0:
        print("Failed to create tracker dir, aborting")
        raise SystemExit
    print("Creating origin files")
    gitignore = open(os.path.join(tracker_dir, ".gitignore"), "w")
    gitignore.write("daemon.log\n")
    gitignore.close()
    run_git("add", "-A")
    run_git("commit", "-m", "Origin")

ts_logfmt = "{0:<" + str(sorted(map(len, games.keys()), reverse = True)[0]) + "} {1}"
class download_thread(threading.Thread):
    def __init__(self, inq, outq):
	super(download_thread, self).__init__()
        self.inq = inq
        self.outq = outq

    def run(self):
        while True:
            appname, url, lm = self.inq.get()

            req = urllib2.Request(url)
            req.add_header("User-Agent", connection_user_agent)
            if lm:
                req.add_header("If-Modified-Since", lm)
            content = ''

            try:
                log.info("Starting " + appname)
                response = urllib2.urlopen(req, timeout = fetch_timeout)
                content = response.read().replace("\r\n", '\n').replace('\r', '\n')
                log.info("Ending " + appname)
                lm = response.headers.get("last-modified", "never")
                log.info("New: " + ts_logfmt.format(appname, lm))
            except urllib2.HTTPError as E:
                code = E.getcode()

                if code == 304:
                    log.info("Old: " + ts_logfmt.format(appname, lm))
                else:
                    log.error("HTTP {0} received".format(code))
            except Exception as E:
                log.error("Unknown error: " + repr(E))

            self.outq.put((appname, content, lm))

            self.inq.task_done()

inqueue = Queue.Queue()
outqueue = Queue.Queue()

for i in range(connection_pool_size):
    t = download_thread(inqueue, outqueue)
    t.daemon = True
    t.start()

def download_urls(urls, lm_store):
    body = {}

    for appname, url  in urls:
        inqueue.put((appname, url, lm_store.get(appname)))

    expected = len(urls)
    received = 0
    maxtries = 5
    usedtries = 0

    while usedtries < maxtries and received < expected:
        try:
            app, content, lm = outqueue.get(timeout = 5)
            body[app] = content
            lm_store[app] = lm
            received += 1
            usedtries = 0
        except Queue.Empty:
            usedtries += 1

    if received != expected:
        log.error("Expected {0} - Got {1} ({2}/{3} retries used)".format(expected, received, usedtries, maxtries))

    return body

def get_ideal_branch_name(label):
    return label.replace(' ', '').lower()

urls = []
for k, v in games.iteritems():
    url = (k, "http://api.steampowered.com/IEconItems_{0}/GetSchema/v0001/?key={1}&language={2}".format(v, api_key, language))
    urls.append(url)

while True:
    clienturls = []
    commit_summary = {}
    schemadata = {}

    log.info("Downloading API schemas...")
    body = download_urls(urls, api_lm_store)

    for k, v in body.iteritems():
        schema_base_name = k + " Schema"
        schema_path = os.path.join(tracker_dir, schema_base_name)

        try:
            commit_summary[k] = {schema_base_name: api_lm_store[k]}
            schemadict = None
            res = str(v)
            olddata = None

            run_git("checkout", get_ideal_branch_name(k))
            if os.path.exists(schema_path):
                with open(schema_path, "rb") as fs:
                    olddata = fs.read()

            if res:
                schemadict = json.loads(res)
                schemadata[schema_base_name] = res
            elif k not in client_schema_urls and olddata:
                schemadict = json.loads(olddata)

            if schemadict:
                client_schema_urls[k] = schemadict["result"]["items_game_url"]

            clienturls.append((k, client_schema_urls[k]))
        except Exception as e:
            log.error("Failing API schema write softly: " + str(e))

    clientbody = {}
    # Don't bother if client url list is empty
    if clienturls:
        log.info("Downloading client schemas...")
        clientbody = download_urls(clienturls, client_lm_store)

    for k, v in clientbody.iteritems():
        res = str(v)
        schema_base_name = k + " Client Schema"

        if res:
            schemadata[schema_base_name] = res

        if k not in commit_summary: commit_summary[k] = {}

        commit_summary[k][schema_base_name] = client_lm_store[k]

    pushready = False
    for game, summary in commit_summary.iteritems():
        ideal_branch_name = get_ideal_branch_name(game)
        files = summary.keys()

        # Checkout branch
        run_git("branch", ideal_branch_name, "master")
        run_git("checkout", ideal_branch_name)

        # Write schemas
        validfiles = []
        for f in files:
            if f in schemadata:
                validfiles.append(f)
                path = os.path.join(tracker_dir, f)
                fstream = open(path, "wb")
                fstream.write(schemadata[f])
                fstream.close()
                del schemadata[f]

        summary_top = ", ".join(validfiles)
        summary_body = "\n\n".join(["{0}: {1}".format(k, v) for k, v in summary.iteritems()])
        if summary_top:
            pushready = True

            log.info("Committing: " + summary_top)
            log.debug(summary_body)

            # Add current game files
            run_git("add", *validfiles)

            # Commit all (just to make sure)
            run_git("commit", "-m", summary_top + "\n\n" + summary_body + "\n")
        else:
            log.info("Nothing changed for " + game)

    # Poosh leetle tracker tree (if push URL is set)
    if tracker_push_url and pushready:
        log.info("Pushing commits...")
        run_git("push", "--porcelain", "--all", tracker_push_url)

    log.info("Sleeping for {0} second(s)".format(schema_check_interval))
    time.sleep(schema_check_interval)
