import discord
from botui import InputModal, SelectView, selection_all, collect_selection_finish, button_pressed, buttons_all, collect_buttons_finish
from guild_games_roles import guilds_roles
import json
from functools import partial
from registration import refresh_player_data
from balancing import shuffle_list, pick_from_top, weighted_player_allocation
from db_utils import jdb_set, jdb_get
from dc_utils import clean_up_msg
import requests
from utils import log
import logging

async def add_players_manually_input_callback(self, interaction: discord.Interaction):
    embed = discord.Embed(title="Adding Players To Game, Please Wait", color=discord.Color.random())
    await interaction.followup.send(embeds=[embed], delete_after = 0)
    data_dict = self.data_dict
    original_interaction = self.original_interaction
    players = set()
    for child in self.children:
        if child.value:
            players.add(child.value.lower())
    
    new_players = players.difference(data_dict["players"])
    data_dict["players"] = data_dict["players"].union(new_players)
    data_dict["selections"][self.current_stage] = new_players.union([child_interface.label for 
                                                      child_interface in self.original_view.children
                                                      if isinstance(child_interface, discord.ui.Button)
                                                      and child_interface.style==discord.ButtonStyle.primary])
    msg = self.original_interaction.message
    await msg.delete()
    await pick_players(data_dict)

async def add_players_manually(self, interaction, data_dict):
    return await interaction.response.send_modal(InputModal(data_dict,
                                                          original_view=self.view,
                                                          original_interaction=interaction,
                                                          callback_func=add_players_manually_input_callback,
                                                          ))


async def players_source(data_dict):
    current_stage = "players_source"
    data_dict["current_stage"] = current_stage
    data_dict["buttons"].setdefault(current_stage, [])
    data_dict["buttons"][current_stage].append({
        "callback_func": selection_all,
        "label": "Select All",
        "style": discord.ButtonStyle.danger})
    data_dict["buttons"][current_stage].append({
        "callback_func": collect_selection_finish,
        "label": "Continue With Current Selection",
        "style": discord.ButtonStyle.success})
    return await data_dict["thread"].send("Choose a Source!", view=SelectView(data_dict))


def players_from_selection(data_dict):
    names = set()
    for v in data_dict["selections"]["players_source"].values() :
        if v == "ocr":
            data_dict["thread"].send("Please reply with the screenshoot", view=SelectView(data_dict))
        else :
            for m in v.members:
                player_name = data_dict["db"].json().get("dcid", "$.{}.quake_name".format(m.id))
                if player_name:
                    names.add(player_name[0])
                else:
                    names.add(m.display_name)
    return data_dict.get("players", set()).union(names)


async def pick_players(data_dict):
    data_dict["players"] = players_from_selection(data_dict)
    current_stage = "pick_players"
    data_dict["current_stage"] = current_stage

    data_dict["buttons"][current_stage] = [{"callback_func": button_pressed,
                                            "label": player,
                                            "style": discord.ButtonStyle.primary
                                            if player in data_dict["selections"].get(current_stage, set())
                                            else discord.ButtonStyle.secondary
                                            }
                                           for player in sorted(data_dict["players"])
                                           ]

    util_buttons = [{"callback_func": buttons_all,
                     "label": "Select All",
                     "style": discord.ButtonStyle.danger},
                    {"callback_func": add_players_manually,
                     "label": "Add Players Manually",
                     "style": discord.ButtonStyle.danger,
                     "defer":False},
                    {"callback_func": collect_buttons_finish,
                     "label": "Continue With Current Selection",
                     "style": discord.ButtonStyle.success}
                    ]
    for button in util_buttons:
        data_dict["buttons"][current_stage].append(button)
    return await data_dict["thread"].send("Pick Players!", view=SelectView(data_dict))


async def choose_balance_func(data_dict):
    current_stage = "choose_balance_func"
    data_dict["current_stage"] = "choose_balance_func"
    players = data_dict["selections"]["pick_players"]
    if len(data_dict["team_balance_options"]) > 1:
        game_modes = {"args":{"max_values":1},
                    "options":{k:k for k in ["Sacrifice", "Sacrifice Tourney","Slipgate", "Objective", "Killing"]}}
        data_dict["dropdowns"][current_stage] = [game_modes]
        data_dict["buttons"][current_stage]=[{
            "callback_func": collect_selection_finish,
            "label": "Continue With Current Selection",
            "style": discord.ButtonStyle.success}]
        return await data_dict["thread"].send("Pick a game mode and Balance Option!", view=SelectView(data_dict))
    else:
        data_dict["balance_func"] = list(
            data_dict["team_balance_options"].values())[0]
        await data_dict["flow"][current_stage](data_dict)


async def get_players_elo(players, db, channel, game_mode): 
    jdb = db.json()
    text = ""
    players_elo = {}
    for player_name in players:
        player_elo_dict = jdb.get("qcelo", "$.['{}']".format(player_name))
        if player_elo_dict:
            player_elo_dict = player_elo_dict[0]
        else:
            try:
                player_data_dict = {"player_data":{"quake_name":player_name},
                                    "channel":channel,
                                    "clean_up":False,
                                    "db":db}
                player_elo_dict = await refresh_player_data(player_data_dict)
                
            except Exception as e:
                logging.exception("failed to get player data: {}".format(str(e)))
                player_elo_dict = {}
            if player_elo_dict=={}:
                text+=f"{player_name} isn't in the DB and failed to get stats "
        players_elo[player_name] = player_elo_dict.get(game_mode,0) 
    return players_elo


async def send_team_balance_recomendations(channel, players_elo, balance_funcs_dict, game_mode_name):
    for balance_func_name,balance_func in balance_funcs_dict.items():
        text = f"\nRecomended teams for {game_mode_name} based on {balance_func_name} algorithm:\n"
        teams = await balance_func(players_elo)
        for team1_elo, team2_elo in teams:
            team1_elo_sum = sum([v for v in team1_elo.values()])
            team2_elo_sum = sum([v for v in team2_elo.values()])
            ideal = (team1_elo_sum + team2_elo_sum)/2 or 1
            distance_from_ideal = abs((abs(team1_elo_sum - team2_elo_sum)/2)/ideal)
            team1 = list(team1_elo.keys())
            team2 = list(team2_elo.keys())
            
            team1.append("Total ELO: {:0.2f}".format(team1_elo_sum))
            team2.append("Total ELO: {:0.2f}".format(team2_elo_sum))
            team1 = ", ".join(team1)
            team2 = ", ".join(team2)
            text+= "\nTeam 1:{}\nTeam 2:{}\nIdeal: {:0.2f}%\n".format(team1,team2, distance_from_ideal*100)
        text+="\n" + ", ".join(["{}:{:0.2f}".format(k,v) for k,v in players_elo.items()])
        await channel.send(text)


async def assign_players(data_dict):
    current_stage = "assign_players"
    
    db = data_dict["db"]
    channel = data_dict["channel"]
    game_mode_name, game_mode = [(k,v) for k,v in data_dict["selections"]["choose_balance_func"].items() if isinstance(v,str)][0]
    balance_funcs_dict = data_dict["team_balance_options"]
    players = data_dict["selections"]["pick_players"]

    players_elo = await get_players_elo(players, db, channel, game_mode)  
    await send_team_balance_recomendations(channel, players_elo, balance_funcs_dict, game_mode_name)
    await clean_up_msg(data_dict)


async def start_pickup(message, db):
    if message.guild:
        thread = await message.create_thread(name="Pickup Organizer", auto_archive_duration=60)
        clean_up = True
    else:
        thread = message.channel
        clean_up = False
    
    author = message.author
    guild = message.guild
    channel = message.channel
    roles = guilds_roles.get(guild.id, {}).get("roles")
    channels = guilds_roles.get(guild.id, {}).get("channels")
    data_dict = {
        "team_balance_options": {"ELO Balanced":weighted_player_allocation, 
                                 "Pick from top":pick_from_top,
                                 "Random": shuffle_list, 
                                 },
        "message": message,
        "thread": thread,
        "author": author,
        "guild": guild,
        "channel": channel,
        "db": db,
        "roles": roles,
        "channels": channels,
        "clean_up":clean_up,
        "selected_players": set(),
        "players": set(),
        "selections": {},
        "dropdowns": {},
        "buttons": {},
    }
    # get players from channels
    sources = {}
    for role in guild.roles:
        if role.id in roles:
            sources[role.name] = role
    for guild_channel in guild.channels:
        if guild_channel.id in channels:
            sources[guild_channel.name] = guild_channel
    data_dict["flow"] = {"players_source": pick_players,
                         "pick_players": choose_balance_func,
                         "choose_balance_func": assign_players}
    data_dict["dropdowns"]["players_source"] = sources

    if len(message.attachments)>0:
        qcocr = """http://tessarest/qcocr/"""
        for atc in message.attachments:
            try:
                url = atc.url
                get_request_url = "{}?url={}".format(qcocr, url)
                

                response_dict = requests.get(get_request_url).json()
                ocr_player_names = [p.lower() for p in response_dict["players_names"]]
                data_dict["players"] = data_dict["players"].union(ocr_player_names)
            except:
                pass


    await players_source(data_dict)


