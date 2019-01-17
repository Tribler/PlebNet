from plebnet.controllers import wallet_controller
from plebnet.utilities.btc import satoshi_to_btc
from strategy import Strategy

from plebnet.settings import plebnet_settings


class ConstantSell(Strategy):
    def __init__(self):
        Strategy.__init__(self)
        self.target_vps_count = int(plebnet_settings.get_instance().strategy_vps_count())

    def apply(self):
        from plebnet.agent.core import attempt_purchase
        self.sell_reputation()
        for i in range(0, self.target_vps_count):
            attempt_purchase()

    def sell_reputation(self):
        self.update_offer()

    def create_offer(self, timeout):
        """
        Retrieve the price of the chosen server to buy and make a new offer on the Tribler marketplace.
        :param timeout: offer to
        :return: None
        """
        if not self.config.get('chosen_provider'):
            return
        wallet = wallet_controller.TriblerWallet(plebnet_settings.get_instance().wallets_testnet_created())
        (provider, option, _) = self.config.get('chosen_provider')
        btc_balance = satoshi_to_btc(wallet.get_balance())
        btc_price = self.get_replication_price(provider, option) * self.target_vps_count - btc_balance
        self.place_offer(btc_price, timeout, self.config)
