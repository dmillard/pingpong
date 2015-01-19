import sqlite3
import trueskill as ts
from random import shuffle
from datetime import datetime
from ranking import Ranking
from flask import *
app = Flask(__name__)

DATABASE = 'pingpong.sqlite'

SCHEMA = '''
PRAGMA foreign_keys = ON;
            
CREATE TABLE IF NOT EXISTS player (
    id INTEGER PRIMARY KEY,
    exposure REAL, -- result of trueskill.exposure()
    alias TEXT UNIQUE, -- msft alias
    nick TEXT UNIQUE, -- nickname
    mu REAL,
    sigma REAL,
    won INTEGER,
    lost INTEGER
);

CREATE INDEX IF NOT EXISTS exposure ON player (exposure);

CREATE TABLE IF NOT EXISTS match (
    id INTEGER PRIMARY KEY,
    winner REFERENCES player,
    loser REFERENCES player,
    winscore INTEGER,
    losescore INTEGER,
    date DATETIME,
    scheduled BOOLEAN
);

CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY,
    p1 REFERENCES player,
    p2 REFERENCES player
);

CREATE INDEX IF NOT EXISTS scheduleplayers ON schedule (p1, p2);

CREATE TABLE IF NOT EXISTS week (
    week INTEGER
);
'''

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.executescript(SCHEMA);
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

@app.route('/', methods=['GET'])
def index():
    db = get_db()
    players = db.execute('SELECT * FROM player ORDER BY exposure DESC;')
    players = Ranking(players.fetchall(), start=1,
            key=lambda x: x['exposure'])
    aliases = db.execute('SELECT alias FROM player ORDER BY alias;')
    recents = db.execute('''
            SELECT w.alias, l.alias, winscore, losescore, scheduled
            FROM match
            JOIN player w ON winner = w.id
            JOIN player l ON loser = l.id
            ORDER BY date DESC LIMIT 10;''')

    # get or regenerate the schedule
    weekrow = db.execute('SELECT * FROM week;').fetchone();
    week = datetime.now().isocalendar()[1]
    if weekrow is None or weekrow['week'] != week:
        db.execute('DELETE FROM schedule;')
        db.execute('DELETE FROM week;')
        db.execute('INSERT INTO week VALUES (?);', (week,))

        players2 = db.execute(
                'SELECT * FROM player ORDER BY exposure DESC;').fetchall()
        if week % 2 == 0: # random week
            shuffle(players2)
        players2 = players2[:len(players2)//2*2] # even the number

        matches = []
        for i in range(0, len(players2), 2):
            p1 = players2[i]
            p2 = players2[i+1]
            matches.append((p1['id'], p2['id']))

        db.executemany('''
            INSERT INTO schedule (p1, p2)
            VALUES (?, ?);''',
            matches)

        db.commit()

    schedule = db.execute('''
        SELECT p1.alias, p2.alias, p1.mu, p1.sigma, p2.mu, p2.sigma
        FROM schedule
        JOIN player p1 ON p1 = p1.id
        JOIN player p2 ON p2 = p2.id;''').fetchall()

    qualities = []
    for match in schedule:
        r1 = ts.Rating(match[2], match[3])
        r2 = ts.Rating(match[4], match[5])
        qualities.append(ts.quality_1vs1(r1, r2) * 100)

    return render_template('index.html',
            players=players, aliases=aliases, recents=recents,
            schedule=zip(schedule, qualities), rankedweek=(week%2==1))


@app.route('/signup', methods=['POST'])
def signup():
    db = get_db()
    try:
        rating = ts.Rating()
        db.execute('''
            INSERT INTO
            player (alias, nick, mu, sigma, exposure, won, lost)
            VALUES (?, ?, ?, ?, ?, 0, 0);''',
            (request.form['alias'],
             request.form['nick'],
             rating.mu,
             rating.sigma,
             ts.expose(rating)))
        db.commit()
    except sqlite3.IntegrityError as e:
        flash(str(e))

    return redirect(url_for('index'))


@app.route('/record', methods=['POST'])
def record():
    db = get_db()

    p1 = request.form['p1']
    s1 = int(request.form['s1'])
    p2 = request.form['p2']
    s2 = int(request.form['s2'])

    hi, lo = max(s1, s2), min(s1, s2)

    if (not ((hi == 2 and lo == 0) or
             (hi == 2 and lo == 1) or
             (hi == 3 and lo == 0) or
             (hi == 3 and lo == 1) or
             (hi == 3 and lo == 2))):
        flash('Ladder is based on 3 or 5 game matches only')
        return redirect(url_for('index'))

    if p1 == p2:
        flash('Match players must be different.')
        return redirect(url_for('index'))

    # check if this was a scheduled match
    scheduledrow = db.execute('''
        SELECT (s.id)
        FROM schedule s
        JOIN player p1 ON s.p1 = p1.id
        JOIN player p2 ON s.p2 = p2.id
        WHERE p1.alias=? AND p2.alias=?
        OR p1.alias=? AND p2.alias=?;''',
        (p1, p2, p2, p1)).fetchone()
    scheduled = scheduledrow is not None
    if scheduled:
        db.execute('DELETE FROM schedule WHERE id=?;', (scheduledrow[0],))

    if s1 > s2:
        win_alias, win_score, lose_alias, lose_score = p1, s1, p2, s2
    else:
        win_alias, win_score, lose_alias, lose_score = p2, s2, p1, s1
    
    date_string = request.form['date'] + ' '
    if ':' in request.form['time']:
        date_string += request.form['time'] + ' '
    else:
        date_string += request.form['time'] + ':00 '
    date_string += 'PM' if 'ampm' in request.form else 'AM'
    try:
        date = datetime.strptime(date_string, "%m/%d/%Y %I:%M %p")
    except:
        flash('Invalid time format')
        return redirect(url_for('index'))
    
    db.execute('''
        INSERT INTO 
        match (winner, loser, winscore, losescore, date, scheduled)
        SELECT w.id, l.id, ?, ?, ?, ?
        FROM player w JOIN player l
        WHERE w.alias = ? AND l.alias = ?;''',
        (win_score, lose_score, date, scheduled, win_alias, lose_alias))

    def get_rating(alias):
        sql = 'SELECT mu, sigma FROM player WHERE alias=?;'
        row = db.execute(sql, (alias,)).fetchone()
        if row is None:
            flash('Alias ' + alias + ' does not exist.')
            return None
        return ts.Rating(mu=row['mu'], sigma=row['sigma'])

    win_rating = get_rating(win_alias)
    lose_rating = get_rating(lose_alias)
    if win_rating is None or lose_rating is None:
        return redirect(url_for('index'))

    win_rating, lose_rating = ts.rate_1vs1(win_rating, lose_rating)
    win_exposure = ts.expose(win_rating);
    lose_exposure = ts.expose(lose_rating);

    db.execute('''
        UPDATE player 
        SET exposure=?, mu=?, sigma=?, won=won+1
        WHERE alias=?;''',
        (win_exposure, win_rating.mu, win_rating.sigma, win_alias));

    db.execute('''
        UPDATE player 
        SET exposure=?, mu=?, sigma=?, lost=lost+1
        WHERE alias=?;''',
        (lose_exposure, lose_rating.mu, lose_rating.sigma, lose_alias));
    
    db.commit()
    
    return redirect(url_for('index'))

@app.route('/matches', methods=['GET'])
def matches():
    db = get_db()
    recents = db.execute('''
            SELECT w.alias, l.alias, winscore, losescore, scheduled, date
            FROM match
            JOIN player w ON winner = w.id
            JOIN player l ON loser = l.id
            ORDER BY date DESC;''')

    return render_template('matches.html', recents=recents)

if __name__ == '__main__':
    # set secret key for sessions
    import string
    import os.path
    from random import SystemRandom
    r = SystemRandom()
    key_chars = string.ascii_letters + string.digits + string.punctuation
    cwd = os.path.abspath(os.path.dirname(__file__))
    try:
        with open(os.path.join(cwd, 'key.json'), 'r') as f:
            app.secret_key = json.load(f)['key']
    except:
        app.secret_key = ''.join(r.choice(key_chars) for i in range(64))
        with open(os.path.join(cwd, 'key.json'), 'w') as f:
            json.dump({'key' : app.secret_key}, f)
    
    app.run(debug=False, host='0.0.0.0')
