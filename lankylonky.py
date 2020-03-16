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

valid_votes = []

bot = commands.Bot(command_prefix='!')
dynamodb = boto3.resource('dynamodb', region_name='us-west-2', endpoint_url=os.getenv('ENDPOINT_URL'))
table = dynamodb.Table('Votes')

# @bot.command(name='start_game', help='start the voting game, setting up who is valid to vote for.')
# @commands.has_role('GM')
# async def start(ctx):
#     global valid_votes
#     for guild in bot.guilds:
#         if guild.name == GUILD:
#             for role in guild.roles:
#                 if role.name == 'player':
#                     for member in role.members:
#                         print(member.nick)
#                         # may need to refactor to allow for members without nicknames
#                         if(member.nick != None):
#                             valid_votes.append(member.nick)
#                     break
#             break
#     print(valid_votes)
#     await ctx.send('added [' + ', '.join(valid_votes) + '] to the game. Let\'s get started!')


@bot.command(name='start_game', help='start the voting game, setting up who is valid to vote for.')
@commands.has_role('GM')
async def start(ctx, role: discord.Role):
    global valid_votes
    if len(valid_votes) != 0:
        valid_votes = []
    for member in role.members:
        print(member.nick)
        player_alias = {'username': member.name, 'nickname':''}
        if(member.nick != None):
            player_alias['nickname'] = member.nick
        valid_votes.append(player_alias)
    player_names = [alias['nickname'] if alias['nickname'] != '' else alias['username'] for alias in valid_votes]
    await ctx.send('I\'ve added the following players to the game. Let\'s get started!\n```\n'+ '\n'.join(player_names) + "```" )

# works if you either use the actual user name (not the nickname), or a mention with nickname
@bot.command(name='vote', help='Vote for someone')
@commands.has_role('player')
async def vote(ctx, player:discord.Member):
    print(player.nick)
    item = table.put_item(
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

@bot.command(name='roll_dice', help='Simulates rolling dice.')
async def roll(ctx, number_of_dice: int, number_of_sides: int):
    dice = [
        str(random.choice(range(1, number_of_sides + 1)))
        for _ in range(number_of_dice)
    ]
    await ctx.send(', '.join(dice))

@bot.command(name='hello')
async def hello(ctx):
    await ctx.send('hello world')

bot.run(TOKEN)