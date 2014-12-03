import sqlite3
import trueskill as ts
from datetime import datetime
from flask import *
app = Flask(__name__)

DATABASE = 'pingpong.sqlite'

SCHEMA = '''
PRAGMA foreign_keys = ON;
            
CREATE TABLE IF NOT EXISTS player (
    id INTEGER PRIMARY KEY,
    rank REAL, -- result of trueskill.exposure()
    alias TEXT UNIQUE, -- msft alias
    nick TEXT UNIQUE, -- nickname
    mu REAL,
    sigma REAL,
    won INTEGER,
    lost INTEGER
);

CREATE INDEX IF NOT EXISTS rank ON player (rank);

CREATE TABLE IF NOT EXISTS match (
    id INTEGER PRIMARY KEY,
    winner REFERENCES player,
    loser REFERENCES player,
    winscore INTEGER,
    losescore INTEGER,
    date DATETIME
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
    players = db.execute('SELECT * FROM player ORDER BY rank DESC;')
    aliases = db.execute('SELECT alias FROM player ORDER BY alias;')

    return render_template('index.html',
            players=players, aliases=aliases)


@app.route('/signup', methods=['POST'])
def signup():
    db = get_db()
    try:
        rating = ts.Rating()
        db.execute('''
            INSERT INTO
            player (alias, nick, mu, sigma, rank, won, lost)
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

    if p1 == p2:
        flash("Aliases playing in a match must be different.")
        return redirect(url_for('index'))

    if s1 > s2:
        win_alias, win_score, lose_alias, lose_score = p1, s1, p2, s2
    else:
        win_alias, win_score, lose_alias, lose_score = p2, s2, p1, s1
    
    date_string = request.form['date'] + ' '
    date_string += request.form['time'] + ' '
    date_string += 'PM' if 'ampm' in request.form else 'AM'
    date = datetime.strptime(date_string, "%m/%d/%Y %I:%M %p")
    
    db.execute('''
        INSERT INTO 
        match (winner, loser, winscore, losescore, date)
        SELECT w.id, l.id, ?, ?, ?
        FROM player w JOIN player l
        WHERE w.alias = ? AND l.alias = ?;''',
        (win_score, lose_score, date, win_alias, lose_alias))

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
    win_rank = ts.expose(win_rating);
    lose_rank = ts.expose(lose_rating);

    db.execute('''
        UPDATE player 
        SET rank=?, mu=?, sigma=?, won=won+1
        WHERE alias=?;''',
        (win_rank, win_rating.mu, win_rating.sigma, win_alias));

    db.execute('''
        UPDATE player 
        SET rank=?, mu=?, sigma=?, lost=lost+1
        WHERE alias=?;''',
        (lose_rank, lose_rating.mu, lose_rating.sigma, lose_alias));
    
    db.commit()
    
    return redirect(url_for('index'))


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
    
    app.run(debug=True)
