#!/usr/bin/python3

import json
import logging
import os
import sys
import time

import krakenex
import requests

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, Job, ConversationHandler, RegexHandler

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger()
# TODO: logger.debug("CHAT_ID: " + str(chat_id))

# Read configuration
with open("config.json") as config_file:
    config = json.load(config_file)

# Connect to Kraken
kraken = krakenex.API()
kraken.load_key("kraken.key")

# Set bot token
updater = Updater(token=config["bot_token"])

# Get dispatcher and job queue
dispatcher = updater.dispatcher
job_queue = updater.job_queue


# Create a button menu to show in Telegram messages
def build_menu(buttons, n_cols=1, header_buttons=None, footer_buttons=None):
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]

    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)

    return menu


# Custom keyboard that shows all available commands
def keyboard_cmds():
    command_buttons = [
        KeyboardButton("/trade"),
        KeyboardButton("/orders"),
        KeyboardButton("/balance"),
        KeyboardButton("/price"),
        KeyboardButton("/value"),
        KeyboardButton("/status")
    ]

    return ReplyKeyboardMarkup(build_menu(command_buttons, n_cols=3))


# Add a custom keyboard with all available commands
def show_cmds():
    chat_id = get_chat_id()

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        updater.bot.send_message(chat_id, text="Access denied")
        return

    msg = "Choose a command"
    mrk = keyboard_cmds()

    updater.bot.send_message(chat_id, msg, reply_markup=mrk)


# Check order status and send message if changed
def monitor_order(bot, job):
    req_data = dict()
    req_data["txid"] = job.context["order_txid"]

    # Send request to get info on specific order
    res_data = kraken.query_private("QueryOrders", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(job.context["chat_id"], text=res_data["error"][0])
        # Stop this job
        job.schedule_removal()
        return

    # Save information about order
    order_info = res_data["result"][job.context["order_txid"]]

    # Check if order was canceled. If so, stop monitoring
    if order_info["status"] == "canceled":
        # Stop this job
        job.schedule_removal()

    # Check if trade was executed. If so, stop monitoring and send message
    elif order_info["status"] == "closed":
        msg = "Trade executed: " + job.context["order_txid"] + "\n" + trim_zeros(order_info["descr"]["order"])
        bot.send_message(chat_id=job.context["chat_id"], text=msg)
        # Stop this job
        job.schedule_removal()


# Monitor status changes of open orders
def monitor_open_orders():
    if config["check_trade"].lower() == "true":
        # Send request for open orders to Kraken
        res_data = kraken.query_private("OpenOrders")

        # If Kraken replied with an error, show it
        if res_data["error"]:
            updater.bot.send_message(chat_id=config["user_id"], text=res_data["error"][0])
            return

        # Get time in seconds from config
        check_trade_time = config["check_trade_time"]

        if res_data["result"]["open"]:
            for order in res_data["result"]["open"]:
                order_txid = str(order)

                # Create context object with chat ID and order TXID
                context_data = dict(chat_id=config["user_id"], order_txid=order_txid)

                # Create job to check status of order
                job_check_order = Job(monitor_order, check_trade_time, context=context_data)
                job_queue.put(job_check_order, next_t=0.0)


# Remove trailing zeros to get clean values
def trim_zeros(value_to_trim):
    if isinstance(value_to_trim, float):
        return ('%.8f' % value_to_trim).rstrip('0').rstrip('.')
    elif isinstance(value_to_trim, str):
        str_list = value_to_trim.split(" ")
        for i in range(len(str_list)):
            old_str = str_list[i]
            if old_str.replace(".", "").isdigit():
                new_str = str(('%.8f' % float(old_str)).rstrip('0').rstrip('.'))
                str_list[i] = new_str
        return " ".join(str_list)
    else:
        return value_to_trim


# Get balance of all currencies
def balance_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # Command without arguments
    if len(msg_params) == 1:
        # Send request to Kraken to get current balance of all currencies
        res_data = kraken.query_private("Balance")

    # Command with argument 'available'
    elif len(msg_params) == 2 and msg_params[1] == "available":
        req_data = dict()
        req_data["asset"] = "Z" + config["trade_to_currency"]

        # Send request to Kraken to get current trade balance of all currencies
        res_data = kraken.query_private("TradeBalance", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(chat_id, text=res_data["error"][0])
        return

    msg = ""

    # Check for '/trade available'
    # FIXME: Why does this show me the value of my XXBT coins?
    if "tb" in res_data["result"]:
        # tb = trade balance (combined balance of all equity currencies)
        msg = config["trade_to_currency"] + ": " + trim_zeros(res_data["result"]["tb"])
    else:
        for currency_key, currency_value in res_data["result"].items():
            msg += currency_key + ": " + trim_zeros(currency_value) + "\n"

    bot.send_message(chat_id, text=msg)


# Enum for 'trade' command
TRADE_CURRENCY, TRADE_PRICE, TRADE_VOL_TYPE, TRADE_VOLUME, TRADE_EXECUTE = range(5)


# TODO: Add additional button to CANCEL
# Create orders to buy or sell currencies with price limit - choose 'buy' or 'sell'
def trade_buy_sell(bot, update):
    """
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return
    """

    reply_msg = "Buy or sell?"
    reply_mrk = ReplyKeyboardMarkup([["buy", "sell"]])

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_CURRENCY


def trade_currency(bot, update, chat_data):
    chat_data["buysell"] = update.message.text

    user = update.message.from_user
    logger.info("Gender of %s: %s" % (user.first_name, update.message.text))  # TODO: Add correct logging

    reply_msg = "Enter currency"
    reply_mrk = ReplyKeyboardMarkup([["XXBT", "XETH", "XXMR"]])

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_PRICE


def trade_price(bot, update, chat_data):
    chat_data["currency"] = update.message.text

    reply_msg = "Enter price per unit"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_VOL_TYPE


def trade_vol_type(bot, update, chat_data):
    chat_data["price"] = update.message.text

    reply_msg = "How to enter the volume? Or skip and use /all"
    reply_mrk = ReplyKeyboardMarkup([["EURO", "VOLUME"]])

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_VOLUME


def trade_volume(bot, update, chat_data):
    chat_data["vol_type"] = update.message.text

    reply_msg = "Enter volume"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_EXECUTE


def trade_execute(bot, update, chat_data):
    update.message.reply_text("Creating order...", reply_markup=keyboard_cmds())

    # Check if entering the volume was skipped == use whole volume
    if update.message.text == "/all":
        chat_data["vol_type"] = "/all"
        chat_data["volume"] = None
    else:
        chat_data["volume"] = update.message.text

    # Volume specified
    # TODO: Use enum names as strings in dict
    if chat_data["volume"]:
        if chat_data["vol_type"] == "EURO":
            # Volume specified in euro
            amount = float(chat_data["volume"])
            price_per_unit = float(chat_data["price"])
            volume = "{0:.8f}".format(amount / price_per_unit)

        elif chat_data["vol_type"] == "VOLUME":
            # Volume specified in amount of currency
            volume = chat_data["volume"]

    # Volume not specified
    else:
        buy = "buy"
        sell = "sell"

        # Logic for 'buy'
        if chat_data["buysell"] == buy:
            req_data = dict()
            req_data["asset"] = "Z" + config["trade_to_currency"]

            # Send request to Kraken to get current trade balance of all currencies
            res_data = kraken.query_private("TradeBalance", req_data)

            # If Kraken replied with an error, show it
            if res_data["error"]:
                update.message.reply_text(res_data["error"][0])
                return ConversationHandler.END

            euros = res_data["result"]["tb"]
            # Calculate volume depending on full euro balance and round it to 8 digits
            volume = "{0:.8f}".format(float(euros) / float(chat_data["price"]))

        # Logic for 'sell'
        elif chat_data["buysell"] == sell:

            # FIXME: This will give me all available BTC. But some of them are already inside a sell order.
            # FIXME: What i need is not the balance, but the 'free' BTCs that i can still sell
            # Send request to Kraken to get euro balance to calculate volume
            res_data = kraken.query_private("Balance")

            # If Kraken replied with an error, show it
            if res_data["error"]:
                update.message.reply_text(res_data["error"][0])
                return ConversationHandler.END

            # TODO: Use '.upper' all over the place
            current_volume = res_data["result"][chat_data["currency"].upper()]
            # Get volume from balance and round it to 8 digits
            volume = "{0:.8f}".format(float(current_volume))

    req_data = dict()
    req_data["type"] = chat_data["buysell"]
    req_data["pair"] = chat_data["currency"] + "Z" + config["trade_to_currency"]
    req_data["price"] = chat_data["price"]
    req_data["ordertype"] = "limit"
    req_data["volume"] = volume

    # Send request to create order to Kraken
    res_data_add_order = kraken.query_private("AddOrder", req_data)

    # If Kraken replied with an error, show it
    if res_data_add_order["error"]:
        update.message.reply_text(res_data_add_order["error"][0])
        return ConversationHandler.END

    # If there is a transaction id then the order was placed successfully
    if res_data_add_order["result"]["txid"]:
        add_order_txid = res_data_add_order["result"]["txid"][0]

        req_data = dict()
        req_data["txid"] = add_order_txid

        # Send request to get info on specific order
        res_data_query_order = kraken.query_private("QueryOrders", req_data)

        # If Kraken replied with an error, show it
        if res_data_query_order["error"]:
            update.message.reply_text(res_data["error"][0])
            return ConversationHandler.END

        if res_data_query_order["result"][add_order_txid]:
            order_desc = res_data_query_order["result"][add_order_txid]["descr"]["order"]
            update.message.reply_text("Order placed: " + add_order_txid + "\n" + trim_zeros(order_desc))

            if config["check_trade"].lower() == "true":
                # Get time in seconds from config
                check_trade_time = config["check_trade_time"]
                # Create context object with chat ID and order TXID
                context_data = dict(chat_id=update.message.chat_id, order_txid=add_order_txid)

                # Create job to check status of newly created order
                job_check_order = Job(monitor_order, check_trade_time, context=context_data)
                job_queue.put(job_check_order, next_t=0.0)

            return ConversationHandler.END

        else:
            update.message.reply_text("No order with TXID " + add_order_txid)
            return ConversationHandler.END
    else:
        update.message.reply_text("Undefined state: no error and no TXID")

    return ConversationHandler.END


def cancel(bot, update):
    update.message.reply_text("Canceled...", reply_markup=keyboard_cmds())
    return ConversationHandler.END


# TODO: Timeout errors (while calling Kraken) will not get shown because i only handle python-telegram-bot exceptions
def error(bot, update, error):
    logger.error("Update '%s' caused error '%s'" % (update, error))


# Show and manage orders
def orders_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # If there are no parameters, show all orders
    if len(msg_params) == 1:
        # Send request for open orders to Kraken
        res_data = kraken.query_private("OpenOrders")

        # If Kraken replied with an error, show it
        if res_data["error"]:
            bot.send_message(chat_id, text=res_data["error"][0])
            return

        if res_data["result"]["open"]:
            for order in res_data["result"]["open"]:
                order_desc = trim_zeros(res_data["result"]["open"][order]["descr"]["order"])
                bot.send_message(chat_id, text=order + "\n" + order_desc)
            return
        else:
            bot.send_message(chat_id, text="No open orders")
            return
    elif len(msg_params) == 2:
        # If parameter is 'close-all' then close all orders
        if msg_params[1] == "close-all":
            # Send request for open orders to Kraken
            res_data = kraken.query_private("OpenOrders")

            # If Kraken replied with an error, show it
            if res_data["error"]:
                bot.send_message(chat_id, text=res_data["error"][0])
                return

            if res_data["result"]["open"]:
                for order in res_data["result"]["open"]:
                    req_data = dict()
                    req_data["txid"] = order

                    # Send request to Kraken to cancel orders
                    res_data = kraken.query_private("CancelOrder", req_data)

                    # If Kraken replied with an error, show it
                    if res_data["error"]:
                        bot.send_message(chat_id, text=res_data["error"][0])
                        return

                    bot.send_message(chat_id, text="Order closed:\n" + order)
                return
            else:
                bot.send_message(chat_id, text="No open orders")
                return
        else:
            bot.send_message(chat_id, text="Syntax: /orders (['close'] [txid] / ['close-all'])")
            return
    elif len(msg_params) == 3:
        # If parameter is 'close' and TXID is provided, close order with specific TXID
        if msg_params[1] == "close":
            if len(msg_params) == 3:
                req_data = dict()
                req_data["txid"] = msg_params[2]

                # Send request to Kraken to cancel orders
                res_data = kraken.query_private("CancelOrder", req_data)

                # If Kraken replied with an error, show it
                if res_data["error"]:
                    bot.send_message(chat_id, text=res_data["error"][0])
                    return

                bot.send_message(chat_id, text="Order closed:\n" + msg_params[2])
                return
        else:
            bot.send_message(chat_id, text="Syntax: /orders (['close'] [txid] / ['close-all'])")
            return


# Show syntax for all available commands
def syntax_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    syntax_msg = "/balance (['available'])\n"
    syntax_msg += "/trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'eur'])\n"
    syntax_msg += "/orders (['close'] [txid] / 'close-all'])\n"
    syntax_msg += "/price [currency] ([currency] ...)\n"
    syntax_msg += "/value ([currency])\n"
    syntax_msg += "/update\n"
    syntax_msg += "/restart\n"
    syntax_msg += "/status"

    bot.send_message(chat_id, text=syntax_msg)

"""
# TODO: Remove ReplyKeyboard as long as we are in the execution of a command. After complete execution, add it again
# Show the current price for the chosen currency
def price_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    buttons = [
        InlineKeyboardButton("XBT", callback_data="xbt"),
        InlineKeyboardButton("ETH", callback_data="eth"),
        InlineKeyboardButton("XMR", callback_data="xmr")
    ]

    footer = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=3, footer_buttons=footer))
    bot.send_message(chat_id, "Price for which currency?", reply_markup=reply_markup)

    return ONE
"""

# Callback for the 'price' command - choose currency
def price_one(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    req_data = dict()

    # TODO: Create an Enum for this values
    if data == "xbt":
        req_data["pair"] = "XXBT" + "Z" + config["trade_to_currency"]
    elif data == "eth":
        req_data["pair"] = "XETH" + "Z" + config["trade_to_currency"]
    elif data == "xmr":
        req_data["pair"] = "XXMR" + "Z" + config["trade_to_currency"]
    elif data == "cancel":
        bot.edit_message_text("Canceled...", chat_id, message_id)
        return
    else:
        bot.edit_message_text("Unknown callback command...", chat_id, message_id)
        return

    # Send request to Kraken to get current trading price for currency-pair
    res_data = kraken.query_public("Ticker", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(chat_id, text=res_data["error"][0])
        return

    # TODO: It is not necessary to iterate over items - just get the one thing we need
    msg = ""
    for currency_key, currency_value in res_data["result"].items():
        # Get currency without 'trade to currency' value (for example 'ZEUR')
        currency = currency_key[1:-len("Z" + config["trade_to_currency"])]

        # Read last trade price and remove zeros at the end
        last_trade_price = trim_zeros(currency_value["c"][0])

        # Create message
        msg = currency + ": " + last_trade_price
        break

    bot.edit_message_text(message_id=message_id, chat_id=chat_id, text=msg)


# Show the current real money value for all assets combined
def value_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # Send request to Kraken to get current balance of all currencies
    res_data_balance = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data_balance["error"]:
        bot.send_message(chat_id, text=res_data_balance["error"][0])
        return

    curr_str = "Overall: "

    req_data_price = dict()
    req_data_price["pair"] = ""

    for currency_name, currency_amount in res_data_balance["result"].items():
        if currency_name.endswith(config["trade_to_currency"]):
            continue

        if (len(msg_params) == 2) and (currency_name == msg_params[1].upper()):
            req_data_price["pair"] = currency_name + "Z" + config["trade_to_currency"] + ","
            curr_str = msg_params[1].upper() + ": "
            break

        req_data_price["pair"] += currency_name + "Z" + config["trade_to_currency"] + ","

    # Remove last comma from 'pair' string
    req_data_price["pair"] = req_data_price["pair"][:-1]

    # Send request to Kraken to get current trading price for currency-pair
    res_data_price = kraken.query_public("Ticker", req_data_price)

    # If Kraken replied with an error, show it
    if res_data_price["error"]:
        bot.send_message(chat_id, text=res_data_price["error"][0])
        return

    total_value_euro = float(0)

    for currency_pair_name, currency_price in res_data_price["result"].items():
        # Remove trade-to-currency from currency pair to get the pure currency
        currency_without_pair = currency_pair_name[:-len("Z" + config["trade_to_currency"])]
        currency_balance = res_data_balance["result"][currency_without_pair]

        # Calculate total value by multiplying currency asset with last trade price
        total_value_euro += float(currency_balance) * float(currency_price["c"][0])

    # Show only 2 digits after decimal place
    total_value_euro = "{0:.2f}".format(total_value_euro)

    bot.send_message(chat_id, text=curr_str + total_value_euro + " " + config["trade_to_currency"])


# Check if GitHub hosts a different script then the current one
def check_for_update():
    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified (remote file has same hash, is the same version)
    if github_file.status_code == 304:
        # Send message that bot is up to date
        msg = "Bot is up to date"
        updater.bot.send_message(chat_id=config["user_id"], text=msg)
    # Status code 200 = OK (remote file has different hash, is not the same version)
    elif github_file.status_code == 200:
        # Send message that new version is available
        msg = "New version available. Get it with /update"
        updater.bot.send_message(chat_id=config["user_id"], text=msg)
    # Every other status code
    else:
        msg = "Update check not possible. Unexpected status code: " + github_file.status_code
        updater.bot.send_message(chat_id=config["user_id"], text=msg)

"""
# This command will give the user the possibility to check for an update, update or restart the bot
def status_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    buttons = [
        InlineKeyboardButton("Update Check", callback_data="update_check"),
        InlineKeyboardButton("Update", callback_data="update"),
        InlineKeyboardButton("Restart", callback_data="restart"),
        InlineKeyboardButton("Shutdown", callback_data="shutdown")
    ]

    footer = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=footer))
    bot.send_message(chat_id, "Choose an option", reply_markup=reply_markup)

    return ONE
"""


# Callback for the 'status' command - choose a sub-command
def status_one(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    # FIXME: This will not set a new message (on update). Exclude the message from the method and add a return value
    # then call this method and check return value. Show message dependant on return value.
    # TODO: Change in whole app: Only send msg to user from config, not user that send msg
    if data == "update_check":
        check_for_update()
    elif data == "update":
        update(bot, update)
    elif data == "restart":
        restart_cmd(bot, update)
    elif data == "shutdown":
        bot.edit_message_text("Shutting down...", chat_id, message_id)
        exit()  # TODO: Test this
    elif data == "cancel":
        bot.edit_message_text("Canceled...", chat_id, message_id)
        return
    else:
        bot.edit_message_text("Unknown callback command...", chat_id, message_id)
        return


# Download newest script, update the currently running script and restart
def update_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified
    if github_file.status_code == 304:
        msg = "You are running the latest version"
        updater.bot.send_message(chat_id=chat_id, text=msg)
    # Status code 200 = OK
    elif github_file.status_code == 200:
        # Save current ETag (hash) in configuration file
        with open("config.json", "w") as cfg:
            e_tag = github_file.headers.get("ETag")
            config["update_hash"] = e_tag
            json.dump(config, cfg)

        # Get the name of the currently running script
        path_split = os.path.split(str(sys.argv[0]))
        filename = path_split[len(path_split)-1]

        # Save the content of the remote file
        with open(filename, "w") as file:
            file.write(github_file.text)

        # Restart the bot
        restart_cmd(bot, update)
    # Every other status code
    else:
        msg = "Update not executed. Unexpected status code: " + github_file.status_code
        updater.bot.send_message(chat_id=chat_id, text=msg)


# Terminate this script
def shutdown_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    bot.send_message(chat_id, "Shutting down...")

    # Terminate bot
    exit()


# Restart this python script
def restart_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    bot.send_message(chat_id, "Bot is restarting...")
    time.sleep(0.2)
    os.execl(sys.executable, sys.executable, *sys.argv)


# Return chat ID for an Update object
def get_chat_id(update=None):
    if update:
        if update.message:
            return update.message.chat_id
        elif update.callback_query:
            return update.callback_query.from_user["id"]
    else:
        return config["user_id"]


# Add handlers to dispatcher
dispatcher.add_handler(CommandHandler("help", syntax_cmd))
dispatcher.add_handler(CommandHandler("balance", balance_cmd))
dispatcher.add_handler(CommandHandler("orders", orders_cmd))
dispatcher.add_handler(CommandHandler("value", value_cmd))
dispatcher.add_handler(CommandHandler("update", update_cmd))
dispatcher.add_handler(CommandHandler("restart", restart_cmd))
dispatcher.add_handler(CommandHandler("shutdown", shutdown_cmd))

# TRADE command handler
# TODO: Integrate everywhere also the 'CANCEL' command to exit the workflow
trade_handler = ConversationHandler(
    entry_points=[CommandHandler('trade', trade_buy_sell)],
    states={
        TRADE_CURRENCY: [RegexHandler("^(buy|sell)$", trade_currency, pass_chat_data=True)],
        TRADE_PRICE: [RegexHandler("^(XXBT|XETH|XXMR)$", trade_price, pass_chat_data=True)],
        TRADE_VOL_TYPE: [RegexHandler("^(?=.*?\d)\d*[.]?\d*$", trade_vol_type, pass_chat_data=True)],
        TRADE_VOLUME: [RegexHandler("^(EURO|VOLUME)$", trade_volume, pass_chat_data=True),
                       CommandHandler("all", trade_execute, pass_chat_data=True)],
        TRADE_EXECUTE: [RegexHandler("^(((?=.*?\d)\d*[.]?\d*)|(/all))$", trade_execute, pass_chat_data=True)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(trade_handler)

# Log all errors
dispatcher.add_error_handler(error)

"""
# PRICE command handler
price_handler = ConversationHandler(
    entry_points=[CommandHandler('price', price_cmd)],
    states={
        ONE: [CallbackQueryHandler(price_one)]
    },
    fallbacks=[CommandHandler('price', price_cmd)]
)
dispatcher.add_handler(price_handler)

# STATUS command handler
status_handler = ConversationHandler(
    entry_points=[CommandHandler('status', status_cmd)],
    states={
        ONE: [CallbackQueryHandler(status_one)]
    },
    fallbacks=[CommandHandler('status', status_cmd)]
)
dispatcher.add_handler(status_handler)
"""

# Start the bot
updater.start_polling()

# Check if script is the newest version
check_for_update()

# Show all possible commands
show_cmds()

# Monitor status changes of open orders
monitor_open_orders()
