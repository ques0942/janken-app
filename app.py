"""
# じゃんけんゲーム
## /janken/start
    指定されたユーザーでゲームセッションを開始する
## /janken/<session_id>/choice/<user>/<hand>/
    sessionに指定されたユーザーの選択を登録する
    sessionに参加していなかったり、既に登録済みの場合はエラー
## /janken/<session_id>/result
    session参加者が全員手を決めていたら結果を表示
    そうでなければエラー
"""
from __future__ import annotations

import os
import uuid
from enum import Enum
from typing import List, Dict

import werkzeug
from flask import Flask, jsonify
import redis
from redis.lock import Lock

app = Flask(__name__)


class BaseError(Exception):
    pass


class NotInSessionError(BaseError):
    pass


@app.errorhandler(NotInSessionError)
def handle_not_in_session_error(e: NotInSessionError):
    return 'your not in session', 404


class AlreadyChosenError(BaseError):
    pass


@app.errorhandler(AlreadyChosenError)
def handle_already_chosen_error(e: AlreadyChosenError):
    return 'you have already chosen', 400


class SessionAlreadyExistError(BaseError):
    pass


@app.errorhandler(SessionAlreadyExistError)
def handle_session_already_exist_error(e: SessionAlreadyExistError):
    return 'session already exists', 409


class SessionNotFoundError(BaseError):
    pass


@app.errorhandler(SessionNotFoundError)
def handle_session_not_found_error(e: Exception):
    return 'session not found', 404


class CannotStartTransactionError(BaseError):
    pass


@app.errorhandler(CannotStartTransactionError)
def handle_cannot_start_transaction_error(e: CannotStartTransactionError):
    return 'transaction can not start', 500


class TransactionExpiredError(BaseError):
    pass


@app.errorhandler(TransactionExpiredError)
def handle_transaction_expired_error(e: TransactionExpiredError):
    return 'transaction has already expired', 404


class NotInTransactionError(BaseError):
    pass


@app.errorhandler(NotInTransactionError)
def handle_not_in_transaction_error(e: NotInTransactionError):
    return 'not in transaction', 404


class SessionNotClosedError(BaseError):
    pass


@app.errorhandler(SessionNotClosedError)
def handle_session_not_closed_error(e: SessionNotClosedError):
    return 'session is not closed', 400


class UndefinedEnumError(BaseError):
    pass


@app.errorhandler(UndefinedEnumError)
def handle_undefined_enum_error(e: UndefinedEnumError):
    return 'undefined enum error', 500


class Hand(Enum):
    Rock = 1
    Scissors = 2
    Paper = 3

    @classmethod
    def from_str(cls, hand: str) -> Hand:
        hand = hand.lower().strip()
        if hand == "rock":
            return cls.Rock
        elif hand == "scissors":
            return cls.Scissors
        elif hand == "paper":
            return cls.Paper
        raise UndefinedEnumError


class ResultStatus(Enum):
    Draw = 1
    RockWin = 2
    ScissorsWin = 3
    PaperWin = 4


class Choice:
    def __init__(self, user: str, hand: Hand):
        self.__user = user
        self.__hand = hand

    @property
    def user(self) -> str:
        return self.__user

    @property
    def hand(self) -> Hand:
        return self.__hand


class Result:
    def __init__(self, status: ResultStatus, winners: List[str] = None):
        self.__status = status
        self.__winners = winners

    @property
    def status(self) -> ResultStatus:
        return self.__status

    @property
    def winners(self) -> List[str]:
        return self.__winners


class Session:
    __session_id: str
    __users: List[str]
    __choices: Dict[str, Choice]

    def __init__(self, users: List[str]):
        self.__session_id = str(uuid.uuid4())
        if hasattr(users, '__iter__') and not isinstance(users, dict):
            self.__users = users
        else:
            self.__users = []
        self.__choices = {}

    @property
    def session_id(self) -> str:
        return self.__session_id

    @property
    def users(self) -> List[str]:
        return self.__users

    def choose(self, user, hand) -> None:
        if user in self.__users and user not in self.__choices:
            self.__choices[user] = Choice(user, hand)
        elif user in self.__choices:
            raise AlreadyChosenError
        else:
            raise NotInSessionError

    def result(self) -> Result:
        all_users = set(self.__users)
        chose_users = set(self.__choices.keys())
        if all_users == chose_users:
            return self.judge()
        else:
            raise SessionNotClosedError

    def judge(self) -> Result:
        rocks = []
        scissors = []
        papers = []
        for choice in self.__choices.values():
            if choice.hand == Hand.Rock:
                rocks.append(choice.user)
            elif choice.hand == Hand.Scissors:
                scissors.append(choice.user)
            elif choice.hand == Hand.Paper:
                papers.append(choice.user)

        if rocks and scissors and papers:
            return Result(ResultStatus.Draw)
        if rocks and scissors:
            return Result(ResultStatus.RockWin, rocks)
        if scissors and papers:
            return Result(ResultStatus.ScissorsWin, scissors)
        if papers and rocks:
            return Result(ResultStatus.PaperWin, papers)
        return Result(ResultStatus.Draw)


class SessionStore:

    __session_lifetime = 60 * 60
    __lock_timeout = 60 * 5

    def __init__(self):
        self.__redis = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=os.environ.get("REDIS_PORT", 6379),
            password=os.environ.get("REDIS_PASSWORD"),
        )

    def create(self, session: Session) -> None:
        import pickle

        # set value with expiration if same name doesn't exists
        self.__redis.set(
            name=session.session_id,
            value=pickle.dumps(session),
            ex=self.__session_lifetime,
            nx=True
        )

    def store(self, session: Session) -> None:
        import pickle

        if not self.__redis.setex(
                session.session_id,
                self.__session_lifetime,
                pickle.dumps(session),
        ):
            raise SessionAlreadyExistError

    def restore(self, session_id: str) -> Session:
        import pickle

        val = self.__redis.get(session_id)
        if not val:
            raise SessionNotFoundError
        return pickle.loads(val)

    def lock(self, session_id: str) -> Lock:
        return self.__redis.lock(
            name="SESSION_STORE_LOCK:{}".format(session_id),
            timeout=60 * 5,
            blocking_timeout=0,
        )


session_store = SessionStore()


@app.route("/janken/start")
def start():
    from flask import request
    users_param: str = request.args.get('users', '')
    if ',' in users_param:
        users: List[str] = users_param.split(",")
    else:
        users: List[str] = [users_param, ]

    if len(users) < 2:
        return 'this game needs two or more users. request: `{}`'.format(users_param)

    session = Session(users)
    session_store.create(session)
    return "session start: {}".format(session.session_id)


@app.route("/janken/<session_id>/choice/<user>/<hand>")
def choice_hand(session_id: str, user: str, hand: str):
    with session_store.lock(session_id):
        session = session_store.restore(session_id)
        session.choose(user, Hand.from_str(hand))
        session_store.store(session)
    return session.session_id


@app.route("/janken/<session_id>/")
def get(session_id: str):
    session = session_store.restore(session_id)
    res = dict()
    res["session_id"] = session.session_id
    res["users"] = session.users
    return jsonify(res)


@app.route("/janken/<session_id>/result")
def session_result(session_id: str):
    session = session_store.restore(session_id)
    result = session.result()
    res = dict()
    res["status"] = str(result.status)
    res["winner"] = result.winners
    return jsonify(res)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
