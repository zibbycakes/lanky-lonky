import os
import random
import boto3
import json
import traceback

import discord
from dotenv import load_dotenv
from discord.ext import commands
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.conditions import Attr
from datetime import datetime

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')
VOTE_TABLE = os.getenv('VOTE_TABLE')
DAY_TABLE = os.getenv('DAY_TABLE')
STATUS_TABLE = os.getenv('STATUS_TABLE')

valid_votes = []

bot = commands.Bot(command_prefix='!')
dynamodb = boto3.resource('dynamodb')
vote_table = dynamodb.Table(VOTE_TABLE)
day_table = dynamodb.Table(DAY_TABLE)
status_table = dynamodb.Table(STATUS_TABLE)
day_counter = 0
game_started = False
daytime = False
current_game_name = ''
role_for_valid_voters = None

possible_recoveries = []

@bot.event
async def on_ready():
    global possible_recoveries
    # scan to see if there's already a game in progress
    current_statuses = status_table.scan(
        FilterExpression=Attr('Status').eq('InProgress')
    )
    if len(current_statuses['Items']) != 0:
        possible_recoveries = current_statuses['Items']
        print(possible_recoveries)
        # post a message to the channel
        categories = filter(lambda channel: isinstance(channel, discord.CategoryChannel) and channel.name=='MAFIA-GAME', bot.guilds[0].channels)
        category = next(categories)
        channel = discord.utils.get(bot.guilds[0].channels, category=category)
        # sort games by newest to oldest
        possible_recoveries.sort(key=lambda x: x['StatusUpdated'], reverse=True)
        name_list = [game['GameName'] for game in possible_recoveries]
        await channel.send('I\'ve found one or more existing games (names below) '
                           +'that weren\'t finished yet. If you want to recover any of these games, '
                           +'use the command `!recover <game_name> <player_role>`.\n```' +'\n'.join(name_list) +'```')

@bot.command(name='recover', help='Recover a previously started but unfinished game.')
@commands.has_role('Game Master')
async def recover(ctx, game_name, role: discord.Role):
    global current_game_name
    global game_started
    global role_for_valid_voters
    global day_counter
    global daytime
    if game_name in [game['GameName'] for game in possible_recoveries]:
        day_log_items = day_table.query(
            KeyConditionExpression=Key('GameName').eq(game_name),
            ScanIndexForward = False
        )
        if len(day_log_items['Items']) == 0:
            await ctx.send('Recovery failed. No phase information was found.')
            return
        day_counter = day_log_items['Items'][0]['LogDay']
        daytime = day_log_items['Items'][0]['DayStart']

        current_game_name = game_name
        game_started = True
        role_for_valid_voters = role
        evaluate_valid_voters()

        status_update = status_table.update_item(
            Key={
                'GameName': current_game_name
            },
            UpdateExpression="set StatusUpdated = :u",
            ExpressionAttributeValues={
                ':u': datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)")
            }
        )
        print("UpdateItem succeeded:")
        print(json.dumps(status_update, indent=4))

        player_names = [alias['nickname'] if alias['nickname'] != '' else alias['username'] for alias in valid_votes]
        await ctx.send('`' + game_name + '` has been recovered. Here are the stats:\n'
                       +'It is currently ' + (':sunny: Day' if daytime else ':full_moon: Night') + ' '
                       + str(day_counter) +'. ' + 'Below is the list of players.\n```'
                       + '\n'.join(player_names) +'```')
    else:
        await ctx.send('That game is not in a recoverable game.')

@bot.command(name='start_game', help='Start listening for votes. This sets up who is valid to vote for by using the provided role. Needs a unique game name identifier with no spaces, and the role being used by the game players.')
@commands.has_role('Game Master')
async def start(ctx, game_name, role: discord.Role): #verify game name is unique
    global game_started
    if not game_started:
        global valid_votes
        global day_counter
        global daytime
        global current_game_name
        global role_for_valid_voters
        role_for_valid_voters = role
        day_counter = 0
        if len(valid_votes) != 0:
            valid_votes = []
        evaluate_valid_voters()
        if does_game_name_exist(game_name):
            await ctx.send('That game name already exists.')
            return
        current_game_name = game_name
        player_names = [alias['nickname'] if alias['nickname'] != '' else alias['username'] for alias in valid_votes]
        game_started = True
        success = increment_and_record_day() & start_game_entry()
        if success:
            await ctx.send('I\'ve added the following players to the game. Let\'s get started!\n```\n'+ '\n'.join(player_names) + "```\n:sunny: Day " + str(day_counter) + " has been started. Good luck!" )
        else:
            await ctx.send('Something went wrong.')
            game_started = False
            daytime = False
    else:
        await ctx.send("Game not started since there's already one in progress.")

# works if you either use the actual user name (not the nickname), or a mention with nickname
@bot.command(name='vote', help='Vote for someone.')
@commands.bot_has_permissions(read_messages=True)
async def vote(ctx, player:discord.Member):
    global current_game_name
    global daytime
    global game_started
    if game_started:
        if daytime:
            voter_is_valid = is_member_in_voting_pool(ctx.author)
            candidate_is_valid = is_member_in_voting_pool(player)
            if voter_is_valid > -1 and candidate_is_valid > -1:
                item = vote_table.put_item(
                    Item={
                        'GameName':current_game_name,
                        'VotedPlayer':player.display_name,
                        'Timestamp':datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                        'VoterPlayer':ctx.author.display_name
                    }
                )
                print("PutItem succeeded:")
                print(json.dumps(item, indent=4))
                await ctx.send(":ballot_box: `" + ctx.author.display_name +"`'s vote for `" + player.display_name + "` has been registered.")
            elif not voter_is_valid > -1:
                await ctx.send("You (`" + ctx.author.name + "`) are not a valid voter.")
            elif not candidate_is_valid > -1:
                await ctx.send("That person (`" +  player.name + "` / `@" + player.nick + "`) is not a valid option for voting.")
        else:
            await ctx.send("Voting is not permitted at night. Go to sleep.")
    else:
        await ctx.send('No game is currently in progress.')

# make a timestamp for the day, only GM can call
@bot.command(name='start_day', help='Start the next day phase.')
@commands.bot_has_permissions(read_messages=True)
@commands.has_role('Game Master')
async def start_day(ctx):
    global day_counter
    global daytime
    global game_started
    global role_for_valid_voters
    success = increment_and_record_day()
    if success:
        evaluate_valid_voters()
        await ctx.send(":sunny: Day " + str(day_counter)+ " has been started. Good luck!")
    else:
        if daytime:
            await ctx.send("Day was not started since it's already daytime.")
        elif not game_started:
            await ctx.send("Day was not started since a game is not in progress.")
        else:
            await ctx.send("Day was not started.")

# make a timestamp for the end of day, only GM can call. No votes should be allowed at night.
# Changes to the role should be made before end of day
@bot.command(name='end_day', help='End the current day.')
@commands.bot_has_permissions(read_messages=True)
@commands.has_role('Game Master')
async def end_day(ctx):
    global day_counter
    global daytime
    global current_game_name
    if game_started and daytime:
        item = day_table.put_item(
            Item={
                'GameName': current_game_name,
                'LogDay':day_counter,
                'Timestamp':datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                'DayStart': False
            }
        )
        print("PutItem succeeded:")
        print(json.dumps(item, indent=4))
        daytime = False
        tally = tally_votes(day_counter)
        tally_array = [entry['name']+': '+', '.join([voters['voter'] for voters in entry['voters']])
                       + ' ('+str(entry['count']) +')' for entry in tally]
        if len(tally_array) != 0:
            await ctx.send(
                ':full_moon: Day ' + str(day_counter) + ' has ended. Below are the tallied votes.\n```' + '\n'.join(
                    tally_array) + '```')
        else:
            await ctx.send('No one voted today. :man_shrugging:')

@bot.command(name='tally_votes', help='Tally the votes for the current day or, by providing a number, '
                                      +'the final votes for a given day phase.', aliases=['vote_tally'])
async def tally_votes(ctx, day=-1):
    global game_started
    global daytime
    global day_counter
    if game_started:
        if day == -1:
            day = day_counter
        tally = tally_votes(day)
        tally_array = [entry['name'] + ': ' + ', '.join([voters['voter'] for voters in entry['voters']])
                       + ' (' + str(entry['count']) + ')' for entry in tally]
        if len(tally_array) != 0:
            if day == day_counter:
                if daytime:
                    await ctx.send(
                        'Below are the votes counted so far.\n```' + '\n'.join(
                            tally_array) + '```')
                else:
                    await ctx.send(
                        'Below are the votes counted from yesterday.\n```' + '\n'.join(
                            tally_array) + '```')
            else:
                await ctx.send(
                    'Below are the final votes for day ' + str(day) + '.\n```' + '\n'.join(
                        tally_array) + '```')
        elif day == day_counter:
            await ctx.send('No one has voted yet. :man_shrugging:')
        else:
            await ctx.send('That phase hasn\'t happened yet.')
    else:
        await ctx.send('There are no games in progress.')

@commands.has_role('Game Master')
@bot.command(name='remove_player', help='Remove a player from the voters list manually. This will remove all votes from'
             + "or for this player since day start.")
async def remove_player(ctx, player_to_remove: discord.Member):
    index = is_member_in_voting_pool(player_to_remove)
    if index > -1:
        # get all current votes
        # if the VoterPlayer or the VotedPlayer are equal to player_to_remove.display_name, delete the item
        votes_so_far = obtain_all_votes_for_day(day_counter)
        delete_items = []

        for vote in votes_so_far['Items']:
            if vote['VoterPlayer'] == player_to_remove.display_name or vote['VotedPlayer'] == player_to_remove.display_name:
                delete_items.append({
                    'Key': {
                        'GameName': current_game_name,
                        'Timestamp': vote['Timestamp']
                    }
                })
        for delete_item in delete_items:
            vote_table.delete_item(**delete_item)
            print("DeleteItem succeeded:")
            print(json.dumps(delete_item, indent=4))
        del(valid_votes[index])
        await ctx.send('`'+player_to_remove.display_name+'` has been removed from the voters pool.')
    else:
        await ctx.send('That player is currently not in the valid voters pool.')

@commands.has_role('Game Master')
@bot.command(name='add_player', help="Add a voter to the valid voter list manually.")
async def add_player(ctx, player_to_add: discord.Member):
    valid_votes.append({'username': player_to_add.name, 'nickname': player_to_add.name})
    await ctx.send('I\'ve added the player `' + player_to_add.display_name + '` to the valid voter list.')

@commands.has_role('Game Master')
@bot.command(name='end_game', help="End the current game.")
async def end_game(ctx):
    global game_started
    global current_game_name
    global daytime
    if game_started:
        success = end_game_update()
        game_started = False
        daytime = False
        if success:
            await ctx.send('Game over. The game (`'+current_game_name+'`) has successfully been ended.')
        else:
            await ctx.send('There was an issue ending the game.')
    else:
        await ctx.send('There\'s no current game to end.')

def evaluate_valid_voters():
    global role_for_valid_voters
    global valid_votes
    valid_votes = []
    for member in role_for_valid_voters.members:
        # if a player doesn't have a nickname, pretend their username is their nickname by default
        player_alias = {'username': member.name, 'nickname': member.name}
        if (member.nick != None):
            player_alias['nickname'] = member.nick
        valid_votes.append(player_alias)

    # print(str(valid_votes))

def does_game_name_exist(game_name):
    game_name_exists_query = status_table.query(
        KeyConditionExpression=Key('GameName').eq(game_name)
    )
    return len(game_name_exists_query['Items'])>0

def increment_and_record_day():
    global day_counter
    global game_started
    global daytime
    global current_game_name
    if game_started and not daytime:
        day_counter+=1
        item = day_table.put_item(
            Item={
                'GameName': current_game_name,
                'LogDay': day_counter,
                'Timestamp':datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                'DayStart': True
            }
        )
        print("PutItem succeeded:")
        print(json.dumps(item, indent=4))
        daytime = True
        return True
    else:
        return False

def tally_votes(day):
    all_votes_for_day = obtain_all_votes_for_day(day)
    vote_tally = {}
    voters_accounted = []
    for i in all_votes_for_day['Items']:
        if 'VoterPlayer' in i:
            if i['VoterPlayer'] not in voters_accounted:  # only count the vote if it's the latest one
                if i['VotedPlayer'] not in vote_tally:  # add the voted player if the key doesn't exist yet
                    vote_tally[i['VotedPlayer']] = []
                vote_tally[i['VotedPlayer']].append({'voter':i['VoterPlayer'], 'timestamp':i['Timestamp']})  # add the player who voted for them to the dictionary
                voters_accounted.append(i['VoterPlayer'])

    vote_list = [{'name': i, 'count':len(vote_tally[i]), 'voters':vote_tally[i]} for i in vote_tally]
    vote_list.sort(reverse=True, key=lambda x: x['count'])
    return vote_list

def obtain_all_votes_for_day(day):
    global current_game_name
    global daytime
    day_start_ts = -1
    end_ts = -1  # may be end of day, may be now... If night, then end of day ts. If day, now.
    query_get_timestamps = day_table.query(
        KeyConditionExpression=Key('GameName').eq(current_game_name)
    )
    if daytime and day == day_counter:
        for i in query_get_timestamps['Items']:
            if 'LogDay' in i and i['LogDay'] == day:
                if i['DayStart']:
                    day_start_ts = i['Timestamp']
                    continue
        end_ts = datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)")
    else:
        for i in query_get_timestamps['Items']:
            if 'LogDay' in i and i['LogDay'] == day:
                if i['DayStart']:
                    day_start_ts = i['Timestamp']
                else:
                    end_ts = i['Timestamp']
            if day_start_ts != '-1' and end_ts != '-1':
                continue

    if day_start_ts == -1 and end_ts == -1:
        return {'Items':[]}
    votes_query = vote_table.query(
        KeyConditionExpression=Key('GameName').eq(current_game_name) & Key('Timestamp').between(day_start_ts, end_ts),
        ScanIndexForward=False
    )

    return votes_query

def is_member_in_voting_pool(player:discord.Member):
    if player is None:
        print("bad player")
    player_username = player.name
    player_nickname = player.nick
    for i, voter in enumerate(valid_votes):
        if voter['nickname'] == player_nickname or voter['username'] == player_username:
            return i
    return -1

def start_game_entry():
    global current_game_name
    game_status_start_item = status_table.put_item(
        Item={
            'GameName': current_game_name,
            'Status': 'InProgress',
            'GameStarted': datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
            'StatusUpdated': datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)")
        }
    )
    print("PutItem succeeded:")
    print(json.dumps(game_status_start_item, indent=4))
    return True

def end_game_update():
    global current_game_name
    game_status_update_query = status_table.update_item(
        Key={
                'GameName': current_game_name
            },
            UpdateExpression="set #stat = :s, StatusUpdated = :u",
            ExpressionAttributeValues={
                ':s': 'Finished',
                ':u': datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)")
            },
            ExpressionAttributeNames={
            "#stat": "Status" # necessary because 'status' is a reserved word
          }
    )
    print("UpdateItem succeeded:")
    print(json.dumps(game_status_update_query, indent=4))
    return True

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.BadArgument) and ctx.command.name == 'vote':
        await ctx.send(':x: That\'s not a valid input. Make sure you are providing either the username of the player (found before the 4 digit number of their tag), or mentioning the nickname of the player like `@lanky lonky`.')
    elif isinstance(error, commands.errors.BadArgument) and ctx.command.name == 'start_game':
        await ctx.send(':x: That\'s not a valid input. Try mentioning the role like `@Mafia Player`.')
    elif isinstance(error, commands.errors.MissingRequiredArgument) and ctx.command.name == 'start_game':
        await ctx.send(':x: Make sure you include the unique game name and a mention for the role that\'s being used by players for this game. Ex: `!start_game newGame @MafiaPlayer`.')
    elif isinstance(error, commands.errors.MissingRequiredArgument) and ctx.command.name == 'recover':
        await ctx.send(':x: Make sure you include the unique game name and a mention for the role that\'s being used by players for this game. Ex: `!recover newGame @MafiaPlayer`.')
    elif isinstance(error, commands.errors.CommandNotFound):
        await ctx.send(':x: There is no such command.')
    elif isinstance(error, commands.errors.MissingRole):
        await ctx.send('You\'re missing the required role for that command.')
    else:
        print(error)
        print(dir(error))
        print(error.__traceback__)
        traceback.print_tb(error.__traceback__)

bot.run(TOKEN)