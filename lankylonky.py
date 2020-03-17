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

print(VOTE_TABLE)
valid_votes = []

bot = commands.Bot(command_prefix='!')
dynamodb = boto3.resource('dynamodb', region_name='us-west-2', endpoint_url=os.getenv('ENDPOINT_URL'))
vote_table = dynamodb.Table(VOTE_TABLE)

@bot.command(name='start_game', help='start the voting game, setting up who is valid to vote for.')
@commands.has_role('GM')
async def start(ctx, role: discord.Role):
    global valid_votes
    global player_role 
    if len(valid_votes) != 0:
        valid_votes = []
    for member in role.members:
        player_alias = {'username': member.name, 'nickname':''}
        if(member.nick != None):
            player_alias['nickname'] = member.nick
        valid_votes.append(player_alias)
    player_names = [alias['nickname'] if alias['nickname'] != '' else alias['username'] for alias in valid_votes]
    await ctx.send('I\'ve added the following players to the game. Let\'s get started!\n```\n'+ '\n'.join(player_names) + "```" )

# works if you either use the actual user name (not the nickname), or a mention with nickname
@bot.command(name='vote', help='Vote for someone')
@commands.has_role('Mafia Player')
async def vote(ctx, player:discord.Member):
    item = vote_table.put_item(
        Item={
            'VotedPlayer':player.nick,
            'Timestamp':datetime.now().strftime("%d-%b-%Y (%H:%M:%S.%f)"),
            'VoterPlayer':ctx.author.display_name
        }
    )
    print("PutItem succeeded:")
    print(json.dumps(item, indent=4))
    # need to catch error if member name is not correct
    # need to catch error for wrong role type

bot.run(TOKEN)