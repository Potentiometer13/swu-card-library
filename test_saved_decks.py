"""Offline regression checks for complete account-scoped saved-deck retrieval."""
from types import SimpleNamespace
from unittest.mock import patch

from swu_deck_storage import _saved_decks
from swu_inventory import cards_in_saved_decks


def test_pagination_and_inventory():
    rows = [dict(id=str(i), user_id='owner', deck_data={
        'card_progress': {'card': {'inDeck': 1}},
        'sideboard_progress': {'side': {'inDeck': 1}},
    }) for i in range(401)]
    rows.append(dict(id='private', user_id='other', deck_data={}))
    calls = []

    class Query:
        def __init__(self):
            self.user_id = None
        def select(self, fields): return self
        def eq(self, key, value):
            assert key == 'user_id'
            self.user_id = value
            return self
        def order(self, *args, **kwargs): return self
        def range(self, first, last):
            self.first, self.last = first, last
            return self
        def execute(self):
            assert self.user_id == 'owner'
            calls.append((self.first, self.last))
            owned = [row for row in rows if row['user_id'] == self.user_id]
            return SimpleNamespace(data=owned[self.first:self.last + 1])

    client = SimpleNamespace(table=lambda name: Query())
    st = SimpleNamespace(session_state={'swu_auth_user_id': 'owner'})
    with patch('swu_deck_storage._client_from_session', return_value=client):
        result = _saved_decks(st, None)
        assert len(result) == 401
        assert calls == [(0, 199), (200, 399), (400, 599)]
        assert cards_in_saved_decks(st, None) == {'card': 401, 'side': 401}
        assert _saved_decks(st, None) is result
        assert len(calls) == 3, 'Cache should avoid repeated database reads'
    print('PASS: pagination, account scoping, cached reads and complete inventory totals')


if __name__ == '__main__':
    test_pagination_and_inventory()
