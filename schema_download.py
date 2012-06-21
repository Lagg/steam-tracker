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

import json, os, sys, subprocess, time, logging
import pycurl as pc
from cStringIO import StringIO

# Configuration

api_key = None
language = "en_US"
fetch_timeout = 5
games = {"Portal 2": 620,
         "Team Fortress 2": 440,
         "Team Fortress 2 Beta": 520,
         "DOTA 2": 570,
         "DOTA 2 Alt 1": 816,
         "DOTA 2 Beta": 205790
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

log_handler = logging.StreamHandler()
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter("%(levelname)s:\t %(message)s"))

log.addHandler(log_handler)

# Keeps track of last-modified stamps for both schemas
api_lm_store = {}
client_lm_store = {}

# Caches items_game URLs
client_schema_urls = {}

def make_header_dict(value):
    hs = value.splitlines()
    hdict = {}

    for hdr in hs:
        sep = hdr.find(':')
        if sep != -1:
            hdict[hdr[:sep].strip().lower()] = hdr[sep + 1:].strip()

    return hdict

bitbucket = open(os.devnull, "w")

git_env = {"GIT_DIR": tracker_git_dir, "GIT_WORKING_TREE": tracker_dir,
           "GIT_AUTHOR_EMAIL": git_email, "GIT_AUTHOR_NAME": git_name,
           "GIT_COMMITTER_EMAIL": git_email, "GIT_COMMITTER_NAME": git_name}

def run_git(command, *args):
    # Might want to do something about this later with better logging, but right now it's just going to be spam
    code = subprocess.Popen([git_binary, command] + list(args), env = git_env, cwd = tracker_dir, stdout = bitbucket, stderr = bitbucket).wait()
    log.info("Running git {0} ({1})".format(command, code))

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

multi = pc.CurlMulti()

singlestack = []
for i in range(connection_pool_size):
    single = pc.Curl()

    single.setopt(pc.FOLLOWLOCATION, 1)
    single.setopt(pc.USERAGENT, connection_user_agent)
    single.setopt(pc.NOSIGNAL, 1)
    single.setopt(pc.CONNECTTIMEOUT, fetch_timeout)
    single.setopt(pc.TIMEOUT, 240)

    singlestack.append(single)

urlstack = []
for k, v in games.iteritems():
    url = (k, "http://api.steampowered.com/IEconItems_{0}/GetSchema/v0001/?key={1}&language={2}".format(v, api_key, language))
    urlstack.append(url)

freeobjects = singlestack[:]

def download_urls(urls, lm_store):
    body = {}
    headers = {}
    urlcount = len(urls)
    finished_reqs = 0

    while finished_reqs < urlcount:
        while urls and freeobjects:
            appid, url = urls.pop()
            single = freeobjects.pop()
            body[appid] = StringIO()
            headers[appid] = StringIO()
            lm = lm_store.get(appid)

            single.setopt(pc.URL, str(url))
            single.setopt(pc.WRITEFUNCTION, body[appid].write)
            single.setopt(pc.HEADERFUNCTION, headers[appid].write)
            if lm: single.setopt(pc.HTTPHEADER, ["if-modified-since: " + lm])

            single.optf2_label = appid

            log.info("Checking for {0} younger than {1}".format(appid, lm or "now"))

            multi.add_handle(single)

        while True:
            res, handles = multi.perform()
            if res != pc.E_CALL_MULTI_PERFORM:
                break

        while True:
            msgs_rem, done, err = multi.info_read()

            for h in done:
                header = make_header_dict(headers[h.optf2_label].getvalue())
                if 'last-modified' in header:
                    lm_store[h.optf2_label] = header['last-modified']

                response = h.getinfo(pc.RESPONSE_CODE)
                if response == 304:
                    log.info(h.optf2_label + ": Server says nothing new")
                elif response != 200:
                    log.error(h.optf2_label + ": Server returned HTTP " + str(response))
                else:
                    log.info("Done: {0} - Last change: {1}".format(h.optf2_label, lm_store.get(h.optf2_label) or "Eternal"))

                multi.remove_handle(h)
                freeobjects.append(h)

            for h, code, msg in err:
                log.error("Failed: {0} - {1}".format(h.optf2_label, msg))
                multi.remove_handle(h)
                freeobjects.append(h)

            finished_reqs += len(done) + len(err)

            if msgs_rem == 0:
                break

        multi.select(1)

    return headers, body

def get_ideal_branch_name(label):
    return label.replace(' ', '').lower()

while True:
    urls = urlstack[:]
    clienturls = []
    commit_summary = {}
    schemadata = {}

    log.info("Downloading API schemas...")
    headers, body = download_urls(urls, api_lm_store)

    for k, v in body.iteritems():
        schema_base_name = k + " Schema"
        schema_path = os.path.join(tracker_dir, schema_base_name)

        try:
            commit_summary[k] = {schema_base_name: api_lm_store[k]}
            schemadict = None
            res = v.getvalue()

            if res:
                schemadict = json.loads(res)
                schemadata[schema_base_name] = res
            elif k not in client_schema_urls:
                run_git("checkout", get_ideal_branch_name(k))
                if os.path.exists(schema_path):
                    schemadict = json.load(open(schema_path, "rb"))

            if schemadict:
                client_schema_urls[k] = schemadict["result"]["items_game_url"]

            clienturls.append((k, client_schema_urls[k]))
        except Exception as e:
            log.info("Failing API schema write softly: " + str(e))

    log.info("Downloading client schemas...")
    clientheaders, clientbody = download_urls(clienturls, client_lm_store)

    for k, v in clientbody.iteritems():
        res = v.getvalue()
        schema_base_name = k + " Client Schema"

        if res:
            schemadata[schema_base_name] = res

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
                fstream = open(os.path.join(tracker_dir, f), "wb")
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
        run_git("push", "--porcelain", "--mirror", tracker_push_url)

    log.info("Sleeping for {0} second(s)".format(schema_check_interval))
    time.sleep(schema_check_interval)
