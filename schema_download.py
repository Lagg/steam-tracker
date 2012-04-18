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

import json, urllib2, socket, os, sys, subprocess, time, logging

# Configuration

api_key = None
language = "en"
fetch_timeout = 5
games = {"Portal 2": 620,
         "Team Fortress 2": 440,
         "Team Fortress 2 Beta": 520,
         "DOTA 2": 570,
         "DOTA 2 Alt 1": 816
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

# End configuration

# Different handlers can be set for this, maybe for log display in IRC at some point
log = logging.getLogger("schema-daemon")
log.setLevel(logging.INFO)

log_handler = logging.StreamHandler()
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter("%(levelname)s:\t %(message)s"))

log.addHandler(log_handler)

lm_store = {}

def process_schema_request(label, url):
    reqheaders = {}

    try:
        lm = lm_store.get(label)
        if lm: reqheaders["If-Modified-Since"] = lm

        request = urllib2.Request(url, headers = reqheaders)

        log.info("Checking for {0} younger than {1}".format(label, lm or "now"))
        response = urllib2.urlopen(request, timeout = fetch_timeout)

        lm_stamp = response.headers.get("last-modified")
        if lm_stamp: lm_store[label] = lm_stamp

        log.info("Server returned {0} - Last change: {1}".format(label, lm_stamp or "Eternal"))

        schema = response.read()
        schemafile = open(os.path.join(tracker_dir, label), "wb")
        schemafile.write(schema)
        schemafile.close()
    except urllib2.HTTPError as err:
        code = err.getcode()
        if code != 304:
            log.error("{0} server returned HTTP {1}".format(label, code))
        return None
    except urllib2.URLError as err:
        log.error(err)
        return None
    except Exception as err:
        log.error("Unknown error: {0}".format(str(err)))
        return None

    return schema

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

while True:
    for k, v in games.iteritems():
        commit_summary = {}
        ideal_branch_name = k.replace(' ', '').lower()
        url = "http://api.steampowered.com/IEconItems_{0}/GetSchema/v0001/?key={1}&language={2}".format(v, api_key, language)
        schema_base_name = "{0} Schema".format(k)
        client_schema_base_name = "{0} Client Schema".format(k)
        schema_path = os.path.join(tracker_dir, schema_base_name)
        client_schema_path = os.path.join(tracker_dir, client_schema_base_name)
        schemadict = None

        # Checkout branch
        run_git("branch", ideal_branch_name, "master")
        run_git("checkout", ideal_branch_name)

        res = process_schema_request(schema_base_name, url)
	try:
            if res:
                schemadict = json.loads(res)
                commit_summary[schema_base_name] = lm_store[schema_base_name]
            elif os.path.exists(schema_path):
                schemadict = json.load(open(schema_path, "r"))
	except Exception as e:
            log.error("Schema load error: {0}".format(e))

        if schemadict:
            res = process_schema_request(client_schema_base_name, schemadict["result"]["items_game_url"])
            if res:
                commit_summary[client_schema_base_name] = lm_store[client_schema_base_name]

        summary_top = ", ".join(commit_summary.keys())
        summary_body = "\n\n".join(["{0}: {1}".format(k, v) for k, v in commit_summary.iteritems()])
        if summary_top:
            log.info("Preparing commit...")
            log.debug("{0}\n\n{1}\n".format(summary_top, summary_body))

            # Add all working tree files
            run_git("add", "-A")

            # Commit all (just to make sure)
            run_git("commit", "-m", summary_top + "\n\n" + summary_body + "\n")

            # Poosh leetle tracker tree (if push URL is set)
            if tracker_push_url:
                run_git("push", "--porcelain", "--mirror", tracker_push_url)
        else:
            log.info("Nothing changed")

    log.info("Sleeping for {0} second(s)".format(schema_check_interval))
    time.sleep(schema_check_interval)
