# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "requests<3",
# ]
# ///

# Standard
import argparse
import collections
import decimal
import webbrowser
import typing
import xml.etree.ElementTree as xml_tree

# External
import requests

# Global
NS_API = 'http://www.nationstates.net/cgi-bin/api.cgi'
Category = typing.Literal['common', 'uncommon', 'rare', 'ultra-rare', 'epic', 'legendary']
_NSCARD_TAGS = 'CARD', 'CARDID', 'CATEGORY', 'MARKET_VALUE', 'SEASON'


class NSCard(typing.NamedTuple):
    card_id: int
    season: typing.Literal[1, 2, 3]
    value: decimal.Decimal
    category: Category

    @property
    def key(self) -> typing.Tuple[int, typing.Literal[1, 2, 3]]:
        return self.card_id, self.season

    @classmethod
    def from_xml(cls, card: xml_tree.Element):
        card_id, category, value, season = card
        assert (card.tag, card_id.tag, category.tag, value.tag, season.tag) == _NSCARD_TAGS
        return cls(int(card_id.text), int(season.text), value=decimal.Decimal(value.text), category=category.text)


def _main(session: requests.Session, args: 'CmdLineArgs'):
    response = session.get(NS_API + f'?q=cards+deck;nationname={args.nation}')
    response.raise_for_status()
    deck_xml, *extra = xml_tree.fromstring(response.text)
    assert not extra and deck_xml.tag == 'DECK'
    owned = collections.Counter[NSCard](NSCard.from_xml(card) for card in deck_xml)
    for (card_id, season, *_), count in owned.items():
        if count <= 1:
            continue
        webbrowser.open_new_tab(f'https://www.nationstates.net/page=deck/card={card_id}/season={season}?found={count}')
        input('hold ()=={====> ')


class CmdLineArgs(argparse.Namespace):
    nation: str
    user_agent: str | None

    def __init__(self):
        super().__init__()

        parser = argparse.ArgumentParser()
        parser.add_argument('nation', help='declare collector nation')
        parser.add_argument('--user_agent', help="tell NS api who's requesting data")
        parser.parse_args(namespace=self)


if __name__ == '__main__':
    args = CmdLineArgs()
    with requests.Session() as session:
        session.headers['User-Agent'] = args.user_agent or args.nation
        _main(session, args)
