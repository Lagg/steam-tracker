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
games = {
        620: "Portal 2",
        440: "Team Fortress 2",
        520: "Team Fortress 2 Beta",
        570: "DOTA 2",
        816: "DOTA 2 Alt 1",
        205790: "DOTA 2 Beta",
        730: "Counter Strike Global Offensive"
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

# Initial HTTP headers
http_headers = {"User-Agent": connection_user_agent}

# Different handlers can be set for this, maybe for log display in IRC at some point
log = logging.getLogger("schema-daemon")
log.setLevel(logging.INFO)

log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(logging.Formatter("%(levelname)s:\t %(message)s"))

log.addHandler(log_handler)

# Keeps track of last-modified stamps for both schemas
last_modified_store = {}

# Caches items_game URLs
client_schema_urls = {}

bitbucket = open(os.devnull, "w")

git_env = {"GIT_DIR": tracker_git_dir, "GIT_WORKING_TREE": tracker_dir,
           "GIT_AUTHOR_EMAIL": git_email, "GIT_AUTHOR_NAME": git_name,
           "GIT_COMMITTER_EMAIL": git_email, "GIT_COMMITTER_NAME": git_name}

def run_git(command, *args):
    # Might want to do something about this later with better logging, but right now it's just going to be spam
    code = subprocess.call([git_binary, command] + list(args), env = git_env, cwd = tracker_dir, stdout = bitbucket, stderr = bitbucket)
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

def normalize_schema_data(data):
    return data.replace("\r\n", '\n').replace('\r', '\n')

def fetch_normalized(url, lm = None):
    data = None
    code = None

    try:
        req = urllib2.Request(url, headers = http_headers)

        if lm:
            req.add_header("If-Modified-Since", lm)

        response = urllib2.urlopen(req, timeout = fetch_timeout)
        lm = response.headers.get("last-modified")
        code = response.code
        data = normalize_schema_data(response.read())
    except urllib2.HTTPError as E:
        code = E.getcode()
    except Exception as E:
        log.warning("Unexpected error: " + repr(E))

    log.debug("{0}: Code: {1} - LM: {2}".format(url, code, lm))

    return data, lm, code

class download_thread(threading.Thread):
    def __init__(self, inq, outq):
	super(download_thread, self).__init__()
        self.inq = inq
        self.outq = outq

    def run(self):
        while True:
            out = self.inq.get()
            app = out["app"]

            url = "http://api.steampowered.com/IEconItems_{0}/GetSchema/v1/?key={1}&language={2}".format(app, api_key, language)

            log.info("API:{0}: Start".format(app))

            clienturl = out.get("client-url")
            content, lm, code = fetch_normalized(url, out["last-modified"]["api"])

            log.info("API:{0}: End ({1})".format(app, code))

            if content:
                clienturl = json.loads(content)["result"]["items_game_url"]
                out["client-url"] = clienturl
                out["schema"]["api"] = content

            if lm:
                out["last-modified"]["api"] = lm

            if clienturl:
                log.info("Client:{0}: Start".format(app))

                content, lm, code = fetch_normalized(clienturl, out["last-modified"]["client"])

                if content:
                    out["schema"]["client"] = content

                if lm:
                    out["last-modified"]["client"] = lm

                log.info("Client:{0}: End ({1})".format(app, code))

            self.outq.put(out)

            self.inq.task_done()

inqueue = Queue.Queue()
outqueue = Queue.Queue()

for i in range(connection_pool_size):
    t = download_thread(inqueue, outqueue)
    t.daemon = True
    t.start()

def download_schemas():
    for app, name in games.iteritems():
        inobj = {
                "app": app,
                "client-url": client_schema_urls.get(app),
                "schema": {
                    "api": None,
                    "client": None,
                    },
                "last-modified": last_modified_store.setdefault(app, {"api": None, "client": None})
                }

        inqueue.put(inobj)

    expected = len(games)
    received = 0
    maxtries = 5
    usedtries = 0

    while usedtries < maxtries and received < expected:
        try:
            schema = outqueue.get(timeout = 10)
            received += 1
            usedtries = 0

            app = schema["app"]
            name = games[app]
            idealbranch = get_ideal_branch_name(name)

            apibasename = name + " Schema"
            clientbasename = name + " Client Schema"

            run_git("branch", idealbranch, "master")
            run_git("checkout", idealbranch)

            summary = {}
            clienturl = schema["client-url"]
            apidata = schema["schema"]["api"]
            clientdata = schema["schema"]["client"]
            last_modified = schema["last-modified"]
            apits = last_modified["api"]
            clientts = last_modified["client"]

            if apits:
                last_modified_store[app]["api"] = apits

            if clientts:
                last_modified_store[app]["client"] = clientts

            if clienturl:
                client_schema_urls[app] = clienturl

            if apidata:
                with open(os.path.join(tracker_dir, apibasename), "wb") as out:
                    out.write(apidata)
                    summary["API"] = apits or "N/A"

            if clientdata:
                with open(os.path.join(tracker_dir, clientbasename), "wb") as out:
                    out.write(clientdata)
                    summary["Client"] = clientts or "N/A"

            commit_header = ", ".join(summary.keys()) or "None (wait what?)"
            commit_body = '\n\n'.join([type + ": " + ts for type, ts in summary.iteritems()])

            run_git("add", apibasename, clientbasename)
            run_git("commit", "-m", commit_header + "\n\n" + commit_body)
        except Queue.Empty:
            usedtries += 1

    if received != expected:
        log.error("Expected {0} - Got {1} ({2}/{3} retries used)".format(expected, received, usedtries, maxtries))

def get_ideal_branch_name(label):
    return label.replace(' ', '').lower()

while True:
    download_schemas()

    # Poosh leetle tracker tree (if push URL is set)
    if tracker_push_url:
        log.info("Pushing commits...")
        run_git("push", "--porcelain", "--all", tracker_push_url)

    log.debug("LM: " + str(last_modified_store))
    log.debug("URL Cache: " + str(client_schema_urls))

    log.info("Sleeping for {0} second(s)".format(schema_check_interval))
    time.sleep(schema_check_interval)
