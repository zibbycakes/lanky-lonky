import os
import random
import boto3
import json

import discord
from dotenv import load_dotenv
from discord.ext import commands
from boto3.dynamodb.conditions import Key
from datetime import datetime

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')
VOTE_TABLE = os.getenv('VOTE_TABLE')
DAY_TABLE = os.getenv('DAY_TABLE')

valid_votes = []

bot = commands.Bot(command_prefix='!')
dynamodb = boto3.resource('dynamodb', region_name='us-west-2', endpoint_url=os.getenv('ENDPOINT_URL'))
vote_table = dynamodb.Table(VOTE_TABLE)
day_table = dynamodb.Table(DAY_TABLE)
day_counter = 0
game_started = False
daytime = False
current_game_name = ''
role_for_valid_voters = None

@bot.command(name='start_game', help='Start listening for votes. This sets up who is valid to vote for by using the provided role. Needs a unique game name identifier with no spaces, and the role being used by the game players.')
@commands.has_role('GM')
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
        success = increment_and_record_day()
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
    if daytime:
        voter_is_valid = is_member_in_voting_pool(ctx.author)
        candidate_is_valid = is_member_in_voting_pool(player)
        if voter_is_valid and candidate_is_valid:
            item = vote_table.put_item(
                Item={
                    'GameName':current_game_name,
                    'VotedPlayer':player.name,
                    'Timestamp':datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                    'VoterPlayer':ctx.author.display_name
                }
            )
            print("PutItem succeeded:")
            print(json.dumps(item, indent=4))
            await ctx.send(":ballot_box: `" + ctx.author.name +"`'s vote for `" + player.name + "` has been registered.")
        elif not voter_is_valid:
            await ctx.send("You (`" + ctx.author.name + "`) are not a valid voter.")
        elif not candidate_is_valid:
            await ctx.send("That person (`" +  player.name + "` / `@" + player.nick + "`) is not a valid option for voting.")
    else:
        await ctx.send("Voting is not permitted at night. Go to sleep.")

# make a timestamp for the day, only GM can call
@bot.command(name='start_day', help='Start the next day phase.')
@commands.bot_has_permissions(read_messages=True)
@commands.has_role('GM')
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
@commands.has_role('GM')
async def end_day(ctx):
    global day_counter
    global daytime
    global current_game_name
    if game_started and daytime:
        item = day_table.put_item(
            Item={
                'GameName': current_game_name,
                'logDay':day_counter,
                'Timestamp':datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                'dayStart': False
            }
        )
        print("PutItem succeeded:")
        print(json.dumps(item, indent=4))
        daytime = False
        tally = tally_votes(day_counter)
        tally_array = [entry['name']+': '+','.join(entry['voters'])+' ('+str(entry['count']) +')' for entry in tally]
        if len(tally_array) != 0:
            await ctx.send(
                ':full_moon: Day ' + str(day_counter) + ' has ended. Below are the tallied votes.\n```' + '\n'.join(
                    tally_array) + '```')
        else:
            await ctx.send('No one voted today. :man_shrugging:')

@bot.command(name='tally_votes', help='Tally the votes for the current day.', aliases=['vote_tally'])
async def tally_votes(ctx):
    if game_started:
        tally = tally_votes(day_counter)
        tally_array = [entry['name'] + ': ' + ','.join(entry['voters']) + ' (' + str(entry['count']) + ')' for entry in
                       tally]
        if len(tally_array) != 0:
            await ctx.send(
                'Below are the votes counted so far.\n```' + '\n'.join(
                    tally_array) + '```')
        else:
            await ctx.send('No one has voted yet. :man_shrugging:')
    else:
        await ctx.send('There are no games in progress.')

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
    game_name_exists_query = day_table.query(
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
                'logDay': day_counter,
                'Timestamp':datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
                'dayStart': True
            }
        )
        print("PutItem succeeded:")
        print(json.dumps(item, indent=4))
        daytime = True
        return True
    else:
        return False

def tally_votes(day):
    global current_game_name
    global daytime
    day_start_ts = -1
    end_ts = -1 # may be end of day, may be now... If night, then end of day ts. If day, now.
    query_get_timestamps = day_table.query(
        KeyConditionExpression=Key('GameName').eq(current_game_name)
    )
    if daytime and day==day_counter:
        for i in query_get_timestamps['Items']:
            if 'logDay' in i and i['logDay'] == day:
                if i['dayStart']:
                    day_start_ts = i['Timestamp']
                    continue
        end_ts = datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)")
    else:
        for i in query_get_timestamps['Items']:
            if 'logDay' in i and i['logDay'] == day:
                if i['dayStart']:
                    day_start_ts = i['Timestamp']
                else:
                    end_ts = i['Timestamp']
            if day_start_ts != '-1' and end_ts != '-1':
                continue

    votes_query = vote_table.query(
        KeyConditionExpression=Key('GameName').eq(current_game_name) & Key('Timestamp').between(day_start_ts, end_ts),
        ScanIndexForward=False
    )

    vote_tally = {}
    voters_accounted = []
    for i in votes_query['Items']:
        if 'VoterPlayer' in i:
            if i['VoterPlayer'] not in voters_accounted:  # only count the vote if it's the latest one
                if i['VotedPlayer'] not in vote_tally:  # add the voted player if the key doesn't exist yet
                    vote_tally[i['VotedPlayer']] = []
                vote_tally[i['VotedPlayer']].append(i['VoterPlayer'])  # add the player who voted for them to the dictionary
                voters_accounted.append(i['VoterPlayer'])

    vote_generator = [{'name': i, 'count':len(vote_tally[i]), 'voters':vote_tally[i]} for i in vote_tally]

    return vote_generator
        

def is_member_in_voting_pool(player:discord.Member):
    if player is None:
        print("bad player")
    player_username = player.name
    player_nickname = player.nick
    for voter in valid_votes:
        if voter['nickname'] == player_nickname or voter['username'] == player_username:
            return True
    return False

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.BadArgument) and ctx.command.name == 'vote':
        await ctx.send(':x: That\'s not a valid input. Make sure you are providing either the username of the player (found before the 4 digit number of their tag), or mentioning the nickname of the player like `@lanky lonky`.')
    elif isinstance(error, commands.errors.BadArgument) and ctx.command.name == 'start_game':
        await ctx.send(':x: That\'s not a valid input. Try mentioning the role like `@Mafia Player`.')
    elif isinstance(error, commands.errors.MissingRequiredArgument) and ctx.command.name == 'start_game':
        await ctx.send(':x: Make sure you include the unique game name and a mention for the role that\'s being used by players for this game. Ex: `!start_game newGame @MafiaPlayer`.')
    elif isinstance(error, commands.errors.CommandNotFound):
        await ctx.send(':x: There is no such command.')
    else:
        print(error)
        print(dir(error))
        print(error.__traceback__)

bot.run(TOKEN)